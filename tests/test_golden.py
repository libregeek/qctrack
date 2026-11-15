"""Golden report tests (todo 6.2).

``tests/golden/input-report.json`` holds saved measurements for a small library (genuine and
band-limited MP3, Vorbis, truncated, silent, junk and Opus files). Rescoring it needs no audio,
so the screen table, CSV and JSON must come out byte for byte as in the files next to it. A
failure means scoring or report output changed: if that was intended, regenerate with

    QCTRACK_UPDATE_GOLDEN=1 python3 -m pytest tests/test_golden.py

and review the diff before committing. ``tools/make_golden_input.py`` rebuilds the input itself,
which is only needed when the measurements change.
"""

import json
import os
from pathlib import Path

import pytest

from qctrack import __version__
from qctrack.cli.main import main

GOLDEN = Path(__file__).parent / "golden"
INPUT = GOLDEN / "input-report.json"
UPDATE = bool(os.environ.get("QCTRACK_UPDATE_GOLDEN"))


def check(name: str, actual: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.write_text(actual, encoding="utf-8", newline="")
        return
    assert path.exists(), f"missing golden file {name}; run with QCTRACK_UPDATE_GOLDEN=1"
    expected = path.read_text(encoding="utf-8", newline="")
    assert actual == expected, f"{name} differs from the golden file"


def rescore(out: Path, *extra: str) -> None:
    out.mkdir(exist_ok=True)
    code = main(["rescore", str(INPUT), "--color", "never", "-o", str(out), *extra])
    assert code == 1  # the library holds Unsupported files, which exit 1 (REQ-CLI exit codes)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", newline="")


def stable_json(text: str) -> str:
    """The generator line carries the program version; keep the golden file version-free."""
    return text.replace(f'"generator": "qctrack {__version__}"', '"generator": "qctrack"')


def stable_csv(text: str) -> str:
    """The analyzer_version column carries the program version; keep the golden CSV version-free."""
    return text.replace(f",{__version__},", ",VERSION,")


def test_default_profile_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rescore(tmp_path, "--csv", "--csv-values", "--json")
    check("default-screen.txt", capsys.readouterr().out)
    check("default.csv", stable_csv(read(tmp_path / "report.csv")))
    check("default.json", stable_json(read(tmp_path / "report.json")))


def test_custom_profile_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rescore(tmp_path, "--profile", str(GOLDEN / "profile-strict.toml"), "--csv", "--json")
    check("strict-screen.txt", capsys.readouterr().out)
    check("strict.csv", stable_csv(read(tmp_path / "report.csv")))
    check("strict.json", stable_json(read(tmp_path / "report.json")))


def test_golden_input_is_well_formed() -> None:
    doc = json.loads(INPUT.read_text())
    assert doc["format"] == "qctrack-report" and len(doc["tracks"]) == 7
    statuses = {t["status"] for t in doc["tracks"]}
    assert statuses == {"Scored", "Provisional", "Unsupported"}


def test_rescore_with_default_profile_is_stable(tmp_path: Path) -> None:
    """Rescoring the golden report twice gives identical CSV (no clock or order dependence)."""
    a, b = tmp_path / "a", tmp_path / "b"
    rescore(a, "--csv", "--silent")
    rescore(b, "--csv", "--silent")
    assert read(a / "report.csv") == read(b / "report.csv")
