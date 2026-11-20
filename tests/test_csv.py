"""CSV export (todo 5.1, REQ-CSV-01..07, REQ-CLI-03/04/05)."""

import csv
import io
import os
from pathlib import Path

import pytest

from helpers import encode
from qctrack.cli.main import main
from qctrack.core.profile import load_default
from qctrack.report.csv_report import header, safe


def parse(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text, newline="")))


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("csvlib")
    encode(
        root / "a.mp3",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-metadata",
        'title=Hello, "World"\nsecond line',
        "-metadata",
        'artist==HYPERLINK("http://x")',
        "-metadata",
        "album=@SUM(1)",
        "-metadata",
        "track=3",
    )
    encode(root / "b.ogg", "-c:a", "libvorbis", "-q:a", "5", "-metadata", "title=+1+1")
    encode(root / "c.flac", "-c:a", "flac")
    (root / "junk.mp3").write_bytes(b"not audio" * 100)
    return root


def run_csv(args: list[str], capsys: pytest.CaptureFixture[str]) -> list[dict[str, str]]:
    main(["scan", "--no-cache", "--csv=-", *args])
    return parse(capsys.readouterr().out)


def test_stdout_csv_is_rfc_4180_with_crlf_and_one_row_per_file(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["scan", "--no-cache", "--csv=-", str(library)])
    raw = capsys.readouterr().out
    assert raw.endswith("\r\n")
    rows = parse(raw)
    assert [Path(r["file_path"]).name for r in rows] == ["a.mp3", "b.ogg", "c.flac", "junk.mp3"]
    assert {r["status"] for r in rows} == {"Scored", "Unsupported"}
    widths = {len(r) for r in csv.reader(io.StringIO(raw, newline=""))}
    assert len(widths) == 1


def test_screen_output_is_off_with_csv_to_stdout(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["scan", "--no-cache", "--csv=-", str(library)])
    out = capsys.readouterr().out
    assert "files:" not in out
    assert out.startswith("album,title,")


def test_tags_with_commas_quotes_and_line_breaks_round_trip(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = run_csv([str(library / "a.mp3")], capsys)[0]
    assert row["title"] == 'Hello, "World"\nsecond line'
    assert row["track_number"] == "3"


def test_cells_that_look_like_formulas_are_escaped(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = run_csv([str(library / "a.mp3")], capsys)[0]
    assert a["artist"] == '\'=HYPERLINK("http://x")'
    assert a["album"] == "'@SUM(1)"
    b = run_csv([str(library / "b.ogg")], capsys)[0]
    assert b["title"] == "'+1+1"


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@a", "−1", "\tx", "\rx"])
def test_safe_prefixes_formula_starters(text: str) -> None:
    assert safe(text) == "'" + text


def test_safe_leaves_other_text_alone() -> None:
    assert safe("Kind of Blue") == "Kind of Blue"
    assert safe("") == ""
    assert safe("a=b") == "a=b"


def test_not_evaluated_and_unsupported_are_empty_never_zero(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = {Path(r["file_path"]).name: r for r in run_csv([str(library)], capsys)}
    flac = rows["c.flac"]
    assert flac["codec"] == "FLAC"
    assert flac["final_score"] == "" and flac["rating"] == "" and flac["gates_applied"] == "G1"
    assert all(v == "" for k, v in flac.items() if k.endswith("_score"))
    junk = rows["junk.mp3"]
    assert junk["status"] == "Unsupported" and junk["codec"] == "Unknown"
    assert junk["final_score"] == ""
    gone = run_csv([str(library / "missing.mp3")], capsys)[0]
    assert gone["status"] == "Error" and gone["message"] and gone["final_score"] == ""
    assert gone["file_size_bytes"] == "" and gone["codec"] == ""
    quick = run_csv(["--quick", str(library / "a.mp3")], capsys)[0]
    assert quick["F2_07_score"] == ""  # needs the decode, which --quick skips
    assert quick["F1_01_score"] != ""
    assert quick["status"] == "Provisional"


def test_factor_columns_follow_the_catalogue_and_are_always_present(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = run_csv([str(library)], capsys)
    expected = header(load_default())
    assert list(rows[0]) == expected
    factors = [c[:5] for c in expected if c[0] == "F" and c.endswith("_score")]
    assert factors == sorted(factors)
    assert factors[0] == "F1_01" and factors[-1] == "F3_10"


def test_numbers_use_a_dot_and_timestamps_are_iso_with_zone(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = run_csv([str(library / "a.mp3")], capsys)[0]
    assert 0 <= float(row["final_score"]) <= 100
    assert "," not in row["final_score"]
    assert float(row["duration_s"]) > 11
    assert int(row["file_size_bytes"]) == (library / "a.mp3").stat().st_size
    stamp = row["analyzed_at"]
    assert stamp[10] == "T" and (stamp[-6] in "+-") and stamp[-3] == ":"
    assert row["profile"] == "default@2.0"
    # a.mp3 in this library is a genuine 192 kbps encode.
    assert (row["best_copy"], row["action"], row["action_reason"]) == (
        "Low-bitrate encode",
        "Replace: low bitrate",
        "192 kbps avg",
    )
    assert row["O4_score"] != ""
    assert row["analyzer_version"]


def test_value_columns_are_optional(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = run_csv([str(library / "a.mp3")], capsys)[0]
    assert not any(k.endswith("_value") for k in plain)
    with_values = run_csv(["--csv-values", str(library / "a.mp3")], capsys)[0]
    assert with_values["F1_01_value"] == "192 kbps avg"
    assert with_values["F1_01_score"] == plain["F1_01_score"]


def test_default_name_goes_to_output_dir_and_overwrites(library: Path, tmp_path: Path) -> None:
    out = tmp_path / "reports" / "deep"
    assert main(["scan", "--no-cache", "--silent", "--csv", "-o", str(out), str(library)]) == 1
    path = out / "report.csv"
    assert path.is_file()
    path.write_text("old")
    main(["scan", "--no-cache", "--silent", "--csv", "-o", str(out), str(library / "a.mp3")])
    rows = parse(path.read_text(newline=""))
    assert len(rows) == 1  # replaced without asking


def test_custom_name_is_used_as_given(
    library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    main(["scan", "--no-cache", "--silent", "--csv=mine.csv", "-o", "elsewhere", str(library)])
    assert (tmp_path / "mine.csv").is_file()
    assert not (tmp_path / "elsewhere").exists()


def test_report_file_is_utf8_with_crlf_and_optional_bom(library: Path, tmp_path: Path) -> None:
    plain, bom = tmp_path / "p.csv", tmp_path / "b.csv"
    main(["scan", "--no-cache", "--silent", f"--csv={plain}", str(library / "a.mp3")])
    main(["scan", "--no-cache", "--silent", f"--csv={bom}", "--csv-bom", str(library / "a.mp3")])
    raw = plain.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    assert bom.read_bytes().startswith(b"\xef\xbb\xbf")
    parse(raw.decode("utf-8"))


def test_silent_prints_nothing_on_the_terminal(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["scan", "--no-cache", "--silent", str(library)])
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert code == 1  # the exit code still reports the outcome


def test_unwritable_report_is_exit_3(library: Path, tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("running as a user that ignores file permissions")
        assert (
            main(["scan", "--no-cache", "--silent", "--csv", "-o", str(locked), str(library)]) == 3
        )
    finally:
        locked.chmod(0o700)


def test_csv_never_lands_beside_the_audio_unless_asked(
    library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    before = sorted(os.listdir(library))
    main(["scan", str(library), "--no-cache", "--silent", "--csv"])
    assert sorted(os.listdir(library)) == before
    assert (tmp_path / "report.csv").is_file()
