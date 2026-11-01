"""Command-line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from qctrack import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qctrack", description="Audio quality scoring and fake-bitrate detection."
    )
    parser.add_argument("--version", action="version", version=f"qctrack {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
