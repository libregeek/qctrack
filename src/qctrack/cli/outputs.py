"""Output options shared by ``scan`` and ``rescore`` (REQ-CLI-03, -04, -05) and the code that
writes the report files they ask for."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from qctrack.core.analyze import TrackReport
from qctrack.core.profile import Profile
from qctrack.report.csv_report import write_csv
from qctrack.report.html_report import write_html
from qctrack.report.json_report import write_json


def _score(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError(f"{text} is outside 0-100")
    return value


def output_options() -> argparse.ArgumentParser:
    """A parent parser holding the options that decide what is shown and written."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--fail-below",
        type=_score,
        metavar="SCORE",
        help="exit with code 4 if any track scores below SCORE (0-100)",
    )
    p.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colour output; auto = on for a terminal unless NO_COLOR is set (default)",
    )
    p.add_argument(
        "--csv",
        nargs="?",
        const="",
        metavar="FILE",
        help="also write a CSV report (default report.csv); --csv=- writes it to standard "
        "output and turns the screen output off",
    )
    p.add_argument(
        "--csv-values",
        action="store_true",
        help="add a measured-value column next to each factor score in the CSV",
    )
    p.add_argument(
        "--csv-bom", action="store_true", help="start the CSV file with a UTF-8 byte-order mark"
    )
    p.add_argument(
        "--html",
        nargs="?",
        const="",
        metavar="FILE",
        help="also write a self-contained HTML report (default report.html); --html=- writes "
        "it to standard output and turns the screen output off",
    )
    p.add_argument(
        "--json",
        nargs="?",
        const="",
        metavar="FILE",
        help="also write a JSON report with every measurement and score (default report.json); "
        "--json=- writes it to standard output and turns the screen output off",
    )
    p.add_argument(
        "--spectrograms",
        action="store_true",
        help="embed a spectrogram image per track in the HTML report (needs python3-pillow; "
        "makes the scan slower and the report larger)",
    )
    p.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        metavar="DIR",
        help="folder for report files with default names (default: current folder)",
    )
    p.add_argument("--silent", action="store_true", help="no results or progress on the terminal")
    p.add_argument("--profile", type=Path, help="scoring profile (default: user or bundled)")
    p.add_argument("-v", "--verbose", action="store_true", help="show measurements")
    return p


def check_options(args: argparse.Namespace) -> str | None:
    """A message if the combination of options makes no sense, else None."""
    if [args.csv, args.json, args.html].count("-") > 1:
        return "only one report can be written to standard output (-)"
    return None


def screen_off(args: argparse.Namespace) -> bool:
    """True when nothing but a report may go to standard output."""
    return bool(args.silent or "-" in (args.csv, args.json, args.html))


def report_path(arg: str, default_name: str, output_dir: Path | None) -> Path:
    """A custom name is used as given; the default name goes in the output folder."""
    return Path(arg) if arg else (output_dir or Path()) / default_name


def write_reports(
    args: argparse.Namespace,
    reports: Sequence[TrackReport],
    profile: Profile,
    *,
    analyzed_at: str,
    quick: bool,
) -> None:
    """Write every report file the options ask for. Raises ``OSError`` if one cannot be."""
    if args.csv is not None:
        _emit(
            args.csv,
            "report.csv",
            args,
            bom=args.csv_bom,
            write=lambda f: write_csv(
                f, reports, profile, values=args.csv_values, analyzed_at=analyzed_at
            ),
        )
    if args.html is not None:
        _emit(
            args.html,
            "report.html",
            args,
            bom=False,
            write=lambda f: write_html(
                f, reports, profile, analyzed_at=analyzed_at, spectrograms=args.spectrograms
            ),
        )
    if args.json is not None:
        _emit(
            args.json,
            "report.json",
            args,
            bom=False,
            write=lambda f: write_json(f, reports, profile, analyzed_at=analyzed_at, quick=quick),
        )


def _emit(name: str, default: str, args: argparse.Namespace, *, bom: bool, write: object) -> None:
    assert callable(write)
    if name == "-":
        if bom:
            sys.stdout.write("﻿")
        write(sys.stdout)
        return
    if args.output_dir and not name:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    path = report_path(name, default, args.output_dir)
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        write(f)
