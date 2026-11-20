"""JSON report and rescore (todo 5.2, REQ-CLI-06, REQ-PRF-06/07)."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from helpers import encode
from qctrack.cli.main import main
from qctrack.core.profile import load_default


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("jlib")
    encode(
        root / "a.mp3",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-metadata",
        "title=<script>alert(1)</script>",
    )
    encode(root / "b.ogg", "-c:a", "libvorbis", "-q:a", "5")
    encode(root / "c.flac", "-c:a", "flac")
    (root / "junk.mp3").write_bytes(b"not audio" * 100)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=d=3", str(root / "s.mp3")],
        check=True,
    )  # digital silence: -inf dB measurements
    return root


def strict_load(path: Path) -> dict[str, Any]:
    def refuse(name: str) -> object:
        raise AssertionError(f"non-standard JSON constant {name}")

    doc: dict[str, Any] = json.loads(path.read_text(), parse_constant=refuse)
    return doc


def scan(library: Path, out: Path, *extra: str) -> int:
    out.mkdir(exist_ok=True)
    return main(["scan", str(library), "--no-cache", "--silent", "-o", str(out), *extra])


@pytest.fixture(scope="module")
def first_run(library: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("first")
    scan(library, out, "--json", "--csv", "--csv-values")
    return out


def test_report_json_holds_measurements_scores_and_profile(first_run: Path) -> None:
    doc = strict_load(first_run / "report.json")
    assert doc["format"] == "qctrack-report" and doc["schema"] == 1
    profile = load_default()
    assert doc["profile"]["label"] == "default@1.1"
    assert doc["profile"]["sha256"] == profile.content_hash
    assert doc["profile"]["values"]["factors"]["F1.01"] == profile.factors["F1.01"]
    tracks = {Path(t["path"]).name: t for t in doc["tracks"]}
    assert list(tracks) == ["a.mp3", "b.ogg", "c.flac", "junk.mp3", "s.mp3"]
    a = tracks["a.mp3"]
    assert a["status"] == "Scored" and 0 <= a["final_score"] <= 100 and a["rating"]
    assert set(a["objectives"]) == {"O1", "O2", "O3"}
    assert a["factors"] and {"factor", "score", "verdict", "detail"} <= set(a["factors"][0])
    m = a["measured"]
    assert m["tags"]["title"] == "<script>alert(1)</script>"
    assert m["measurements"]["mp3"]["frame_count"] > 0
    assert m["measurements"]["loudness"] and m["measurements"]["spectrum"]
    assert m["size"] == (Path(a["path"])).stat().st_size
    assert tracks["c.flac"]["status"] == "Unsupported" and tracks["c.flac"]["measured"]["detection"]
    assert doc["summary"]["tracks"] == 5


def test_silent_track_gives_strict_json(first_run: Path) -> None:
    doc = strict_load(first_run / "report.json")  # raises on -Infinity / NaN constants
    silent = next(t for t in doc["tracks"] if t["path"].endswith("s.mp3"))
    loud = silent["measured"]["measurements"]["loudness"][0]
    assert loud["integrated_lufs"] == "-Infinity"


def test_rescore_with_same_profile_reproduces_the_csv_without_the_audio(
    library: Path, first_run: Path, tmp_path: Path
) -> None:
    moved = tmp_path / "moved"
    library.rename(moved)
    try:
        assert (
            main(["rescore", str(first_run / "report.json"), "--silent", "-o", str(tmp_path)]) == 1
        )
        code = main(
            [
                "rescore",
                str(first_run / "report.json"),
                "--silent",
                "--csv-values",
                "--csv",
                f"--json={tmp_path / 'again.json'}",
            ]
        )
        assert code == 1
    finally:
        moved.rename(library)
    # the CSV of a scan and of its rescore are identical, including the original timestamp
    out = tmp_path / "re"
    out.mkdir()
    main(
        [
            "rescore",
            str(first_run / "report.json"),
            "--silent",
            "--csv",
            "--csv-values",
            "-o",
            str(out),
        ]
    )
    assert (out / "report.csv").read_bytes() == (first_run / "report.csv").read_bytes()
    assert (
        strict_load(tmp_path / "again.json")["tracks"]
        == strict_load(first_run / "report.json")["tracks"]
    )


def test_rescore_with_another_profile_changes_scores_and_records_it(
    first_run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prof = tmp_path / "p.toml"
    prof.write_text('name = "mine"\nversion = "2"\n[objectives]\nO1 = 100\nO2 = 0\nO3 = 0\n')
    out = tmp_path / "o"
    out.mkdir()
    assert (
        main(
            ["rescore", str(first_run / "report.json"), "--silent", "--profile", str(prof)]
            + ["--json", "--csv", "-o", str(out)]
        )
        == 1
    )
    before = strict_load(first_run / "report.json")["tracks"][0]
    after = strict_load(out / "report.json")
    assert after["profile"]["label"] == "mine@2"
    assert after["profile"]["sha256"] != load_default().content_hash
    assert after["tracks"][0]["final_score"] != before["final_score"]
    assert after["tracks"][0]["measured"] == before["measured"]  # measurements untouched
    assert "mine@2" in (out / "report.csv").read_text()


def test_rescore_defaults_to_report_json_in_the_output_folder(first_run: Path) -> None:
    assert main(["rescore", "--silent", "-o", str(first_run), "--fail-below", "100"]) == 4


def test_rescore_screen_output_matches_the_scan(
    library: Path, first_run: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["scan", str(library), "--no-cache", "-j", "1"])
    scanned = capsys.readouterr().out
    main(["rescore", str(first_run / "report.json")])
    assert capsys.readouterr().out == scanned


BAD_REPORTS = [
    None,
    "not json",
    "[1, 2]",
    '{"format": "other"}',
    '{"format": "qctrack-report", "schema": 99}',
    '{"format": "qctrack-report", "schema": 1, "tracks": [{}], "profile": {}}',
]


@pytest.mark.parametrize("content", BAD_REPORTS)
def test_unreadable_reports_are_a_usage_error(
    content: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "r.json"
    if content is not None:
        f.write_text(content)
    assert main(["rescore", str(f)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("qctrack: ") and "Traceback" not in err


def test_bad_profile_is_a_usage_error(first_run: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[objectives]\nO1 = -5\n")
    assert main(["rescore", str(first_run / "report.json"), "--profile", str(bad)]) == 2


def test_two_reports_cannot_share_standard_output(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", str(library), "--csv=-", "--json=-"]) == 2
    assert "standard output" in capsys.readouterr().err


def test_json_to_stdout_turns_the_screen_off(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["scan", str(library / "a.mp3"), "--no-cache", "--json=-"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["tracks"][0]["path"].endswith("a.mp3")


def test_worker_failures_survive_a_rescore(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qctrack.cli import workers

    monkeypatch.setattr(workers, "TARGET", _always_crash)
    out = tmp_path / "o"
    out.mkdir()
    main(["scan", str(library / "a.mp3"), "--no-cache", "--silent", "--json", "-o", str(out)])
    track = strict_load(out / "report.json")["tracks"][0]
    assert track["status"] == "Error" and "SIGSEGV" in track["message"]
    assert main(["rescore", "--silent", "-o", str(out), "--json=" + str(out / "r2.json")]) == 1
    assert strict_load(out / "r2.json")["tracks"][0]["message"] == track["message"]


def _always_crash(path: Path, quick: bool) -> object:
    import os
    import signal

    os.kill(os.getpid(), signal.SIGSEGV)
    raise AssertionError  # pragma: no cover
