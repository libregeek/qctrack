"""The man page (man/qctrack.1) renders and is up to date with the argument parser."""

import shutil
import subprocess
from pathlib import Path

import pytest

from qctrack import __version__
from qctrack.cli.main import build_parser

MAN = Path(__file__).resolve().parent.parent / "man" / "qctrack.1"


def _option_strings() -> set[str]:
    parser = build_parser()
    parsers = [parser]
    for action in parser._actions:  # noqa: SLF001 - argparse has no public listing
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            parsers.extend(choices.values())
    return {
        s
        for p in parsers
        for a in p._actions
        for s in a.option_strings
        if s.startswith("--") and s != "--help"
    }  # noqa: SLF001


def test_every_option_is_in_the_page() -> None:
    text = MAN.read_text(encoding="utf-8").replace("\\-", "-")
    missing = sorted(o for o in _option_strings() if o not in text)
    assert not missing, f"man page is out of date; run tools/make_manpage.sh: {missing}"


def test_page_has_current_version() -> None:
    assert f"qctrack {__version__}" in MAN.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("man") is None, reason="man is not installed")
def test_page_renders_without_warnings() -> None:
    r = subprocess.run(
        ["man", "--warnings", "-l", str(MAN)],
        capture_output=True,
        text=True,
        env={"MANWIDTH": "80", "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert r.returncode == 0
    assert r.stderr.strip() == ""
    assert "EXIT STATUS" in r.stdout
