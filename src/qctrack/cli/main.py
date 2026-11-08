"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path

from qctrack import __version__
from qctrack.cli.progress import Progress
from qctrack.cli.table import format_table
from qctrack.core.analyze import TrackReport, analyze_file
from qctrack.core.detect import Detection
from qctrack.core.mp3scan import Mp3Scan
from qctrack.core.oggscan import OggScan
from qctrack.core.profile import (
    ProfileError,
    default_profile_text,
    load_profile,
    resolve_profile,
)
from qctrack.core.scoring import Status
from qctrack.core.tags import StoredGain, TagInfo, lame_gains

# Used only to choose which files inside a folder are worth examining. The format of
# a file is always decided from its content (REQ-FMT-04). Files named on the command
# line are examined regardless of extension.
_FOLDER_CANDIDATE_EXTENSIONS = frozenset({".mp3", ".mp2", ".ogg", ".oga", ".opus", ".spx", ".flac"})


EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, EXIT_FATAL, EXIT_BELOW = 0, 1, 2, 3, 4


def _score(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError(f"{text} is outside 0-100")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qctrack", description="Audio quality scoring and fake-bitrate detection."
    )
    parser.add_argument("--version", action="version", version=f"qctrack {__version__}")
    sub = parser.add_subparsers(dest="command")
    scan = sub.add_parser("scan", help="analyse files and folders")
    scan.add_argument("paths", nargs="+", type=Path, help="files and folders (recursive)")
    scan.add_argument("--quick", action="store_true", help="header-only scan (pass 1)")
    scan.add_argument(
        "--fail-below",
        type=_score,
        metavar="SCORE",
        help="exit with code 4 if any track scores below SCORE (0-100)",
    )
    scan.add_argument("--profile", type=Path, help="scoring profile (default: user or bundled)")
    scan.add_argument("-v", "--verbose", action="store_true", help="show measurements")
    scan.add_argument(
        "--follow-links", action="store_true", help="follow symbolic links to folders"
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
            for root, dirs, files in os.walk(p, followlinks=follow_links):
                dirs.sort()
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
        f"truncated: {'yes' if r.truncated else 'no'}"
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


def _factor_lines(report: TrackReport) -> list[str]:
    lines = ["factors:"]
    for f in report.factors:
        score = f"{f.score:5.1f}" if f.score is not None else "  n/e"
        verdict = f.verdict.value if f.verdict else "-"
        lines.append(f"  {f.factor}  {score}  {verdict:<4}  {f.detail}")
    s = report.score
    objs = ", ".join(f"{k} {v:.1f}" if v is not None else f"{k} -" for k, v in s.objectives.items())
    lines.append(f"objectives: {objs}; coverage {s.coverage_pct:.0f}%")
    return lines


def _table_row(report: TrackReport) -> tuple[list[str], str]:
    """Table cells (file, album, title, score, rating, gates) and a note for the Notes column."""
    s = report.score
    tags = report.tags
    head = [str(report.path), tags.album, tags.title]
    if s.status is Status.ERROR:
        return [*head, "-", "Error", ""], report.message
    if s.status is Status.UNSUPPORTED:
        assert report.detection is not None
        note = describe(report.detection).removeprefix("Unsupported (").removesuffix(")")
        return [*head, "-", "Unsupported", ";".join(s.gates)], note
    if s.final_score is None or s.rating is None:
        return [*head, "-", "Unscored", ""], ""
    rating = s.rating + ("*" if s.status is Status.PROVISIONAL else "")
    return [*head, f"{s.final_score:.1f}", rating, ";".join(s.gates)], ""


def _print_table(reports: Sequence[TrackReport]) -> None:
    rows, notes = zip(*(_table_row(r) for r in reports), strict=True)
    headers = ["File", "Album", "Title", "Score", "Rating", "Gates"]
    table = [list(r) for r in rows]
    if any(notes):
        headers.append("Notes")
        table = [[*r, n] for r, n in zip(table, notes, strict=True)]
    width = shutil.get_terminal_size().columns if sys.stdout.isatty() else None
    shrink = [i for i in (0, 1, 2, 6) if i < len(headers)]
    for line in format_table(
        headers, table, right=[3], shrink=shrink, keep_end=[0], max_width=width
    ):
        print(line)
    if any(r.score.status is Status.PROVISIONAL for r in reports):
        print("* provisional: less than the required share of the weight was evaluated")


def _summary(reports: Sequence[TrackReport]) -> str:
    ratings: Counter[str] = Counter()
    other: Counter[Status] = Counter()
    for r in reports:
        if r.score.rating:
            ratings[r.score.rating] += 1
        if r.score.status in (Status.UNSUPPORTED, Status.ERROR, Status.UNSCORED):
            other[r.score.status] += 1
    graded = ", ".join(f"{k} {ratings[k]}" for k in "ABCDF")
    provisional = sum(1 for r in reports if r.score.status is Status.PROVISIONAL)
    return (
        f"{len(reports)} files: {graded}; provisional {provisional}, "
        f"unsupported {other[Status.UNSUPPORTED]}, errors {other[Status.ERROR]}"
    )


def _print_details(report: TrackReport) -> None:
    m = report.measurements
    if m is None:
        return
    if m.mp3 is not None:
        lines = _mp3_lines(m.mp3)
        lame_gain = lame_gains(m.mp3.lame) if m.mp3.lame else []
    else:
        assert m.ogg is not None
        lines, lame_gain = _ogg_lines(m.ogg), []
    lines += _tag_lines(report.tags, lame_gain) + _factor_lines(report)
    print(report.path)
    for line in lines:
        print(f"    {line}")
    print()


def _scan(args: argparse.Namespace) -> int:
    try:
        profile = resolve_profile(args.profile)
    except ProfileError as e:
        print(f"qctrack: {e}", file=sys.stderr)
        return EXIT_USAGE
    files = list(discover(args.paths, args.follow_links))
    progress = Progress(len(files), sys.stderr)
    reports: list[TrackReport] = []
    try:
        for number, path in enumerate(files, start=1):
            progress.update(number, path)
            reports.append(analyze_file(path, profile, quick=args.quick))
    finally:
        progress.done()
    if reports:
        _print_table(reports)
        print()
    if args.verbose:
        for report in reports:
            _print_details(report)
    print(_summary(reports))
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
