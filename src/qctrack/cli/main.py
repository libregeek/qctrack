"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

from qctrack import __version__
from qctrack.core.detect import Detection, detect_file

# Used only to choose which files inside a folder are worth examining. The format of
# a file is always decided from its content (REQ-FMT-04). Files named on the command
# line are examined regardless of extension.
_FOLDER_CANDIDATE_EXTENSIONS = frozenset({".mp3", ".mp2", ".ogg", ".oga", ".opus", ".spx", ".flac"})


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
        "--follow-links", action="store_true", help="follow symbolic links to folders"
    )
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


def _scan(args: argparse.Namespace) -> int:
    problems = 0
    for path in discover(args.paths, args.follow_links):
        try:
            d = detect_file(path)
        except OSError as e:
            print(f"{path}\tError ({e.strerror})")
            problems += 1
            continue
        print(f"{path}\t{describe(d)}")
        if not d.supported:
            problems += 1
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return _scan(args)
    parser.print_usage(sys.stderr)
    return 2
