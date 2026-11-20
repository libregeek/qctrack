"""Command-line entry point."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path

from qctrack import __version__
from qctrack.cli.outputs import (
    check_options,
    output_options,
    report_path,
    screen_off,
    write_reports,
)
from qctrack.cli.progress import Progress
from qctrack.cli.style import RATING_STYLE, Painter, should_color
from qctrack.cli.table import format_table
from qctrack.cli.workers import DEFAULT_MEMORY_MB, DEFAULT_TIMEOUT_S, WorkerPool
from qctrack.core.analyze import Measured, TrackReport, score_measured
from qctrack.core.cache import CacheError, ResultCache, file_key
from qctrack.core.detect import Detection
from qctrack.core.mp3scan import Mp3Scan
from qctrack.core.oggscan import OggScan
from qctrack.core.profile import (
    Profile,
    ProfileError,
    default_profile_text,
    load_profile,
    resolve_profile,
)
from qctrack.core.scoring import Status, gate_legend
from qctrack.core.storage import classify, default_jobs
from qctrack.core.storage import describe as describe_storage
from qctrack.core.tags import StoredGain, TagInfo, lame_gains
from qctrack.report.csv_report import now_iso
from qctrack.report.json_report import ReportFileError, read_json

# Used only to choose which files inside a folder are worth examining. The format of
# a file is always decided from its content (REQ-FMT-04). Files named on the command
# line are examined regardless of extension.
_FOLDER_CANDIDATE_EXTENSIONS = frozenset({".mp3", ".mp2", ".ogg", ".oga", ".opus", ".spx", ".flac"})


EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, EXIT_FATAL, EXIT_BELOW = 0, 1, 2, 3, 4
EXIT_INTERRUPTED = 130  # 128 + SIGINT, the shell convention


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be above 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qctrack", description="Audio quality scoring and fake-bitrate detection."
    )
    parser.add_argument("--version", action="version", version=f"qctrack {__version__}")
    sub = parser.add_subparsers(dest="command")
    common = output_options()
    scan = sub.add_parser("scan", parents=[common], help="analyse files and folders")
    scan.add_argument("paths", nargs="+", type=Path, help="files and folders (recursive)")
    scan.add_argument("--quick", action="store_true", help="header-only scan (pass 1)")
    scan.add_argument(
        "-j",
        "--jobs",
        type=_positive_int,
        metavar="N",
        help="parallel workers (default: one per CPU core on SSDs, two on spinning disks "
        "and network shares)",
    )
    scan.add_argument(
        "--timeout",
        type=_positive_float,
        default=DEFAULT_TIMEOUT_S,
        metavar="SECONDS",
        help=f"time budget per file (default {DEFAULT_TIMEOUT_S:g}); a slower file is an Error",
    )
    scan.add_argument(
        "--no-cache", action="store_true", help="ignore and do not update the result cache"
    )
    scan.add_argument(
        "--follow-links", action="store_true", help="follow symbolic links to folders"
    )
    rescore = sub.add_parser(
        "rescore",
        parents=[common],
        help="score a saved report.json again with another profile, without the audio",
    )
    rescore.add_argument(
        "file",
        nargs="?",
        type=Path,
        help="report.json to read (default: report.json in the output folder)",
    )
    profile = sub.add_parser("profile", help="scoring profiles")
    psub = profile.add_subparsers(dest="profile_command", required=True)
    psub.add_parser("show-default", help="print the default profile")
    check = psub.add_parser("validate", help="check a profile file")
    check.add_argument("file", type=Path, help="profile to check")
    return parser


def discover(paths: Sequence[Path], follow_links: bool) -> Iterator[Path]:
    for p in paths:
        if p.is_dir():
            seen: set[tuple[int, int]] = set()  # real folders entered, to stop link loops
            for root, dirs, files in os.walk(p, followlinks=follow_links):
                dirs.sort()
                if follow_links:
                    try:
                        st = os.stat(root)
                    except OSError:
                        dirs.clear()
                        continue
                    if (st.st_dev, st.st_ino) in seen:
                        dirs.clear()
                        continue
                    seen.add((st.st_dev, st.st_ino))
                for name in sorted(files):
                    if Path(name).suffix.lower() in _FOLDER_CANDIDATE_EXTENSIONS:
                        yield Path(root) / name
        else:
            yield p


def describe(d: Detection) -> str:
    if d.supported:
        return d.format.value
    if d.ogg_streams and d.format.supported:
        names = ", ".join(s.value for s in d.ogg_streams)
        return f"Unsupported (multiplexed Ogg: {names}; supported in a later release)"
    return f"Unsupported ({d.format.value}; supported in a later release)"


def _mp3_lines(r: Mp3Scan) -> list[str]:
    if r.frame_count == 0:
        return ["no MPEG audio frames found"]
    br = f"{r.average_bitrate_kbps:.0f} kbps avg"
    if r.mode.value == "VBR":
        lo, hi = min(r.bitrate_histogram), max(r.bitrate_histogram)
        br += f" ({lo}-{hi} kbps)"
    lines = [
        f"codec: {r.codec}, {r.sample_rate} Hz, {r.channel_mode}",
        f"bitrate: {br}, mode {r.mode.value}",
    ]
    enc = r.encoder or "none stored"
    if r.lame:
        extra = [x for x in (r.lame.preset_name, f"{r.lame.vbr_method}") if x]
        enc += f" ({', '.join(extra)})"
        lines.append(f"encoder: {enc}")
        lowpass = f"{r.lame.lowpass_hz} Hz" if r.lame.lowpass_hz else "none declared"
        lines.append(f"lowpass: {lowpass}")
    else:
        lines.append(f"encoder: {enc}")
    counted = f"counted {r.counted_duration_s:.2f} s ({r.frame_count} frames)"
    if r.declared_frames is None or r.declared_duration_s is None:
        lines.append(f"length: {counted}; none declared")
    else:
        mism = (r.length_mismatch or 0) * 100
        lines.append(
            f"length: declared {r.declared_duration_s:.2f} s ({r.declared_frames} frames), "
            f"{counted}, mismatch {mism:.2f}%"
        )
    lines.append(
        f"structure: {r.sync_losses} sync losses, {r.unidentified_bytes} unidentified bytes "
        f"({r.unidentified_fraction * 100:.3f}%), truncated: "
        f"{'yes, ' + str(r.truncated_bytes) + ' bytes missing' if r.truncated else 'no'}"
    )
    if r.missing_frames:
        lines.append(f"declared but missing: {r.missing_frames} frames ({r.missing_s or 0:.2f} s)")
    if r.metadata_bytes:
        meta = ", ".join(f"{k} {v}" for k, v in sorted(r.metadata_bytes.items()))
        lines.append(f"metadata bytes: {meta}")
    return lines


def _ogg_lines(r: OggScan) -> list[str]:
    lines: list[str] = []
    layout = f"{len(r.streams)} stream(s) in {r.link_count} link(s)"
    if r.chained:
        layout += ", chained"
    if r.multiplexed:
        layout += ", multiplexed"
    lines.append(f"layout: {layout}, {r.pages} pages")
    for s in r.vorbis_streams:
        v = s.vorbis
        if v is None:
            continue
        nominal = (
            f"{v.bitrate_nominal // 1000} kbps nominal" if v.bitrate_nominal > 0 else "no nominal"
        )
        dur = s.duration_s or 0.0
        lines.append(
            f"vorbis {s.serial:#x}: version {v.version}, {v.rate} Hz, {v.channels} ch, "
            f"{nominal}, {dur:.2f} s"
        )
        lines.append(f"  encoder: {s.vendor or 'none stored'}")
    lines.append(f"bitrate: {r.average_bitrate_kbps:.0f} kbps avg, length {r.duration_s:.2f} s")
    lines.append(
        f"structure: {r.crc_failures} CRC failures, {r.lost_pages} lost pages, "
        f"{r.sequence_errors} sequence errors, {r.orphan_pages} orphan pages, "
        f"{r.granule_backwards} granule reversals"
    )
    lines.append(
        f"unidentified: {r.unidentified_bytes} bytes ({r.unidentified_fraction * 100:.3f}%), "
        f"truncated: {'yes, ' + str(r.truncated_bytes) + ' bytes missing' if r.truncated else 'no'}"
        f", end-of-stream flag: {'set' if r.last_ended else 'missing'}"
    )
    return lines


def _tag_lines(t: TagInfo, lame_gain: list[StoredGain]) -> list[str]:
    lines = [f"tags: {', '.join(t.formats) if t.formats else 'none'}"]
    if t.formats:
        shown = [
            f"{label} {value}"
            for label, value in (
                ("title", t.title),
                ("artist", t.artist),
                ("album", t.album),
                ("track", t.track_number),
            )
        ]
        lines.append("  " + ", ".join(shown))
        if t.pictures:
            lines.append(f"  pictures: {t.pictures} ({t.picture_bytes} bytes)")
    gains = t.gains + lame_gain
    if gains:
        for g in gains:
            gain = f"{g.gain_db:+.2f} dB" if g.gain_db is not None else "no gain"
            peak = f", peak {g.peak:.6f}" if g.peak else ""
            lines.append(f"  stored gain ({g.source}, {g.scope}): {gain}{peak}")
    else:
        lines.append("  stored gain: none")
    if t.problems:
        lines.append("  tag problems: " + "; ".join(t.problems))
    return lines


def _factor_lines(report: TrackReport, paint: Painter) -> list[str]:
    lines = [paint("factors:", "bold")]
    for f in report.factors:
        score = f"{f.score:5.1f}" if f.score is not None else "  n/e"
        name = f.verdict.value if f.verdict else "-"
        verdict = paint.verdict(f"{name:<4}", name)
        lines.append(f"  {paint(f.factor, 'dim')}  {score}  {verdict}  {f.detail}")
    s = report.score
    objs = ", ".join(f"{k} {v:.1f}" if v is not None else f"{k} -" for k, v in s.objectives.items())
    lines.append(f"objectives: {objs}; coverage {s.coverage_pct:.0f}%")
    return lines


def _table_row(report: TrackReport) -> tuple[list[str], str, str]:
    """Table cells (file, album, title, score, rating, gates), a note for the Notes column,
    and a tone (the rating letter, or ``error``/``unsupported``/``unscored``) for colour."""
    s = report.score
    tags = report.tags
    head = [str(report.path), tags.album, tags.title]
    if s.status is Status.ERROR:
        return [*head, "-", "Error", ""], report.message, "error"
    if s.status is Status.UNSUPPORTED:
        assert report.detection is not None
        note = describe(report.detection).removeprefix("Unsupported (").removesuffix(")")
        return [*head, "-", "Unsupported", ";".join(s.gates)], note, "unsupported"
    if s.final_score is None or s.rating is None:
        return [*head, "-", "Unscored", ""], "", "unscored"
    rating = s.rating + ("*" if s.status is Status.PROVISIONAL else "")
    return [*head, f"{s.final_score:.1f}", rating, ";".join(s.gates)], "", s.rating


_FILE, _SCORE, _RATING, _GATES, _NOTES = 0, 3, 4, 5, 6


def _print_table(reports: Sequence[TrackReport], paint: Painter) -> None:
    rows, notes, tones = zip(*(_table_row(r) for r in reports), strict=True)
    headers = ["File", "Album", "Title", "Score", "Rating", "Gates"]
    table = [list(r) for r in rows]
    if any(notes):
        headers.append("Notes")
        table = [[*r, n] for r, n in zip(table, notes, strict=True)]
    fancy = sys.stdout.isatty() and (sys.stdout.encoding or "").lower().replace("-", "") == "utf8"

    def style(col: int, row: int, text: str) -> str:
        if col == -1:
            return paint(text, "dim")  # the rule under the header
        if row == -1:
            return paint(text, "bold")
        tone = tones[row]
        if col == _FILE:  # quiet folder, readable file name
            folder, slash, name = text.rpartition("/")
            return paint(folder + slash, "dim") + name
        if col == _SCORE:
            hue = [c for c in RATING_STYLE.get(tone, ()) if c != "bold"]
            return paint(text, *hue)  # same hue as the rating, but the badge stays bolder
        if col == _RATING:
            if tone in RATING_STYLE:
                return paint.rating(text, tone)
            return paint(text, "red") if tone == "error" else paint(text, "cyan")
        if col == _GATES:
            return paint(text, "yellow")
        if col == _NOTES:
            return paint(text, "dim")
        return text

    width = shutil.get_terminal_size().columns if sys.stdout.isatty() else None
    shrink = [i for i in (0, 1, 2, 6) if i < len(headers)]
    for line in format_table(
        headers,
        table,
        right=[_SCORE],
        shrink=shrink,
        keep_end=[0],
        max_width=width,
        style=style,
        rule_char="─" if fancy else "-",
    ):
        print(line)
    if any(r.score.status is Status.PROVISIONAL for r in reports):
        print(
            paint("* provisional: less than the required share of the weight was evaluated", "dim")
        )


def _summary(reports: Sequence[TrackReport], paint: Painter | None = None) -> str:
    paint = paint or Painter(False)
    ratings: Counter[str] = Counter()
    other: Counter[Status] = Counter()
    for r in reports:
        if r.score.rating:
            ratings[r.score.rating] += 1
        if r.score.status in (Status.UNSUPPORTED, Status.ERROR, Status.UNSCORED):
            other[r.score.status] += 1

    def count(letter: str) -> str:
        text = f"{letter} {ratings[letter]}"
        return paint.rating(text, letter) if ratings[letter] else paint(text, "dim")

    graded = ", ".join(count(k) for k in "ABCDF")
    provisional = sum(1 for r in reports if r.score.status is Status.PROVISIONAL)
    errors = other[Status.ERROR]
    err_text = f"errors {errors}"
    return (
        f"{paint(f'{len(reports)} files:', 'bold')} {graded}; provisional {provisional}, "
        f"unsupported {other[Status.UNSUPPORTED]}, "
        f"{paint(err_text, 'red') if errors else err_text}"
    )


def _legend(reports: Sequence[TrackReport], profile: Profile, paint: Painter) -> str | None:
    """A line explaining the gates that appear in ``reports``; None when there are none."""
    used = sorted({g for r in reports for g in r.score.gates})
    if not used:
        return None
    meaning = gate_legend(profile)
    items = "; ".join(f"{paint(g, 'yellow')} {meaning[g]}" for g in used)
    return f"{paint('Gates:', 'bold')} {items}"


def _print_details(report: TrackReport, paint: Painter) -> None:
    m = report.measurements
    if m is None:
        return
    if m.mp3 is not None:
        lines = _mp3_lines(m.mp3)
        lame_gain = lame_gains(m.mp3.lame) if m.mp3.lame else []
    else:
        assert m.ogg is not None
        lines, lame_gain = _ogg_lines(m.ogg), []
    lines += _tag_lines(report.tags, lame_gain) + _factor_lines(report, paint)
    print(paint(str(report.path), "bold"))
    for line in lines:
        print(f"    {line}")
    print()


def _open_cache(disabled: bool) -> ResultCache | None:
    if disabled:
        return None
    try:
        return ResultCache()
    except CacheError as e:
        print(f"qctrack: warning: {e}; continuing without the cache", file=sys.stderr)
        return None


def _from_cache(
    cache: ResultCache | None,
    files: Sequence[Path],
    quick: bool,
    profile: Profile,
    reports: dict[int, TrackReport],
    spectrograms: bool = False,
) -> list[tuple[int, tuple[str, int, int] | None]]:
    """Fill ``reports`` from the cache; return the (file index, cache key) still to analyse."""
    todo: list[tuple[int, tuple[str, int, int] | None]] = []
    for i, path in enumerate(files):
        hit = cache.get(path, quick, spectrograms) if cache is not None else None
        if hit is not None:
            if not spectrograms and hit.measurements is not None:
                hit.measurements.spectrogram = None  # not asked for: keep reports small
            reports[i] = score_measured(hit, profile)
        else:
            todo.append((i, file_key(path) if cache is not None else None))
    return todo


def _store(
    cache: ResultCache, key: tuple[str, int, int], quick: bool, measured: Measured
) -> ResultCache | None:
    """Save one result. If the cache fails, say so once and carry on without it."""
    try:
        cache.put(key, quick, measured)
    except CacheError as e:
        print(f"qctrack: warning: {e}; continuing without the cache", file=sys.stderr)
        cache.close()
        return None
    return cache


def _print_screen(
    reports: Sequence[TrackReport], profile: Profile, paint: Painter, verbose: bool
) -> None:
    if reports:
        _print_table(reports, paint)
        print()
    if verbose:
        for report in reports:
            _print_details(report, paint)
    print(_summary(reports, paint))
    legend = _legend(reports, profile, paint)
    if legend:
        print(legend)


def _scan(args: argparse.Namespace) -> int:
    problem = check_options(args)
    if problem:
        print(f"qctrack: {problem}", file=sys.stderr)
        return EXIT_USAGE
    try:
        profile = resolve_profile(args.profile)
    except ProfileError as e:
        print(f"qctrack: {e}", file=sys.stderr)
        return EXIT_USAGE
    spectrograms = args.spectrograms
    if spectrograms and args.quick:
        print(
            "qctrack: warning: --spectrograms needs a full scan; ignored with --quick",
            file=sys.stderr,
        )
        spectrograms = False
    if spectrograms and importlib.util.find_spec("PIL") is None:
        print("qctrack: --spectrograms needs Pillow (install python3-pillow)", file=sys.stderr)
        return EXIT_USAGE
    files = list(discover(args.paths, args.follow_links))
    by_index: dict[int, TrackReport] = {}
    jobs = args.jobs
    if jobs is None:
        kinds = [classify(p) for p in args.paths]
        jobs = default_jobs(kinds)
        if args.verbose:
            print(f"qctrack: {jobs} worker(s), storage: {describe_storage(kinds)}", file=sys.stderr)
    cache = _open_cache(args.no_cache)
    try:
        todo = _from_cache(cache, files, args.quick, profile, by_index, spectrograms)
        progress = Progress(len(todo), sys.stderr)
        progress.enabled = progress.enabled and not args.silent
        try:
            with WorkerPool(
                jobs,
                quick=args.quick,
                spectrograms=spectrograms,
                timeout_s=args.timeout,
                memory_mb=DEFAULT_MEMORY_MB,
            ) as pool:
                for result in pool.run([files[i] for i, _ in todo], progress.update):
                    index, key = todo[result.index]
                    measured = result.measured or Measured(files[index], error=result.error)
                    by_index[index] = score_measured(measured, profile)
                    if cache is not None and key is not None and result.measured is not None:
                        cache = _store(cache, key, args.quick, measured)
        finally:
            progress.done()
    except KeyboardInterrupt:
        # Every finished result is already committed; a rerun picks up from here.
        print(
            f"qctrack: interrupted after {len(by_index)} of {len(files)} file(s); "
            "run the same command again to resume",
            file=sys.stderr,
        )
        return EXIT_INTERRUPTED
    finally:
        if cache is not None:
            cache.close()
    reports = [by_index[i] for i in sorted(by_index)]  # discovery order
    return _finish(args, reports, profile, analyzed_at=now_iso(), quick=args.quick)


def _finish(
    args: argparse.Namespace,
    reports: Sequence[TrackReport],
    profile: Profile,
    *,
    analyzed_at: str,
    quick: bool,
) -> int:
    """Screen output, report files and the exit code, for ``scan`` and ``rescore``."""
    paint = Painter(should_color(args.color, sys.stdout))
    if not screen_off(args):
        _print_screen(reports, profile, paint, args.verbose)
    try:
        write_reports(args, reports, profile, analyzed_at=analyzed_at, quick=quick)
    except OSError as e:
        where = f"{e.filename}: " if e.filename else ""
        print(f"qctrack: cannot write report: {where}{e.strerror or e}", file=sys.stderr)
        return EXIT_FATAL
    problems = any(r.score.status in (Status.UNSUPPORTED, Status.ERROR) for r in reports)
    if args.fail_below is not None:
        below = [
            r
            for r in reports
            if r.score.final_score is not None and r.score.final_score < args.fail_below
        ]
        if below:
            # Takes precedence over code 1: the caller asked for this check by name.
            print(
                f"qctrack: {len(below)} track(s) scored below {args.fail_below:g}",
                file=sys.stderr,
            )
            return EXIT_BELOW
    return EXIT_PROBLEMS if problems else EXIT_OK


def _rescore(args: argparse.Namespace) -> int:
    problem = check_options(args)
    if problem:
        print(f"qctrack: {problem}", file=sys.stderr)
        return EXIT_USAGE
    try:
        profile = resolve_profile(args.profile)
    except ProfileError as e:
        print(f"qctrack: {e}", file=sys.stderr)
        return EXIT_USAGE
    source = args.file or report_path("", "report.json", args.output_dir)
    try:
        saved = read_json(source)
    except ReportFileError as e:
        print(f"qctrack: {e}", file=sys.stderr)
        return EXIT_USAGE
    if args.spectrograms and not any(
        t.measurements is not None and t.measurements.spectrogram for t in saved.tracks
    ):
        print(
            "qctrack: warning: the report holds no spectrograms; scan with --spectrograms "
            "and --json first",
            file=sys.stderr,
        )
    reports = [score_measured(m, profile) for m in saved.tracks]
    return _finish(args, reports, profile, analyzed_at=saved.analyzed_at, quick=saved.quick)


def _profile(args: argparse.Namespace) -> int:
    if args.profile_command == "show-default":
        sys.stdout.write(default_profile_text())
        return 0
    try:
        profile = load_profile(args.file)
    except ProfileError as e:
        print(f"qctrack: {e}", file=sys.stderr)
        return EXIT_USAGE
    print(f"{args.file}: OK ({profile.label}, sha256 {profile.content_hash[:12]})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)  # a usage error exits with code 2
    try:
        if args.command == "scan":
            return _scan(args)
        if args.command == "rescore":
            return _rescore(args)
        if args.command == "profile":
            return _profile(args)
    except BrokenPipeError:
        # The reader went away (``| head``). Keep Python from complaining again at exit.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_FATAL
    except Exception as e:  # last resort: report, do not dump a traceback
        print(f"qctrack: fatal error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_FATAL
    parser.print_usage(sys.stderr)
    return EXIT_USAGE
