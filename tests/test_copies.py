"""Ranking copies of one track: ``compare`` and ``--duplicates`` (requirements 4.5, 5.10)."""

import csv
import subprocess
from pathlib import Path

import pytest

from qctrack.cli.main import main
from qctrack.core.analyze import TrackReport, analyze_file
from qctrack.core.copies import apply_groups, find_duplicates, rank
from qctrack.core.profile import load_default

P = load_default()
NOISE = "anoisesrc=d={d}:c=pink:r=44100:a=0.3:seed={s}"
LEVEL = "volume='0.35+0.3*sin(2*PI*t/7)':eval=frame"


def _make(
    out: Path,
    *args: str,
    seconds: int = 20,
    gain: str = "",
    tags: tuple[str, str] = ("Some Artist", "Song"),
) -> Path:  # noqa: E501
    graph = f"[0][1]amerge=inputs=2,{LEVEL}" + (f",volume={gain}" if gain else "")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", NOISE.format(d=seconds, s=1),
         "-f", "lavfi", "-i", NOISE.format(d=seconds, s=2), "-filter_complex", graph,
         "-metadata", f"artist={tags[0]}", "-metadata", f"title={tags[1]}", *args, str(out)],
        check=True,
    )  # fmt: skip
    return out


@pytest.fixture(scope="module")
def copies(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("copies")
    out = {
        "320": _make(d / "song_320.mp3", "-c:a", "libmp3lame", "-b:a", "320k"),
        "256": _make(d / "song_256.mp3", "-c:a", "libmp3lame", "-b:a", "256k"),
        "128": _make(d / "song_128.mp3", "-c:a", "libmp3lame", "-b:a", "128k"),
        "q6": _make(d / "song_q6.ogg", "-c:a", "libvorbis", "-q:a", "6"),
        # A remaster: 4 dB louder and 3 s longer.
        "remaster": _make(d / "song_remaster.mp3", "-c:a", "libmp3lame", "-b:a", "320k",
                          seconds=23, gain="4dB"),
        "other": _make(d / "other.mp3", "-c:a", "libmp3lame", "-b:a", "320k",
                       tags=("Other", "Tune")),
    }  # fmt: skip
    fake = d / "song_fake320.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(out["128"]), "-c:a", "libmp3lame",
         "-b:a", "320k", str(fake)],
        check=True,
    )  # fmt: skip
    out["fake"] = fake
    return out


def reports(paths: list[Path]) -> list[TrackReport]:
    return [analyze_file(p, P) for p in paths]


def test_genuine_beats_a_transcode_and_a_lower_bitrate(copies: dict[str, Path]) -> None:
    g = rank(reports([copies["fake"], copies["128"], copies["320"]]), P)
    assert [r.path.name for r in g.members] == [
        "song_320.mp3",
        "song_128.mp3",  # a genuine 128 ranks above a transcode (authenticity before quality)
        "song_fake320.mp3",
    ]
    assert g.decision.step == "encode quality" and g.tied == 1
    assert g.summary == "keep song_320.mp3: encode quality: content to 20.0 kHz against 16.8 kHz"


def test_small_differences_are_equivalent(copies: dict[str, Path]) -> None:
    g = rank(reports([copies["320"], copies["256"]]), P)
    assert g.decision.sign == 0 and g.summary == "Equivalent, keep either"
    three = rank(reports([copies["320"], copies["256"], copies["128"]]), P)
    assert three.tied == 2 and three.summary.startswith("Equivalent, keep any of ")


def test_different_masters_and_track_warnings(copies: dict[str, Path]) -> None:
    g = rank(reports([copies["320"], copies["remaster"]]), P)
    assert g.different_masters
    apply_groups([g])
    assert all(r.advice and "different_masters" in r.advice.flags for r in g.members)
    other = rank(reports([copies["320"], copies["other"]]), P)
    assert any("artist tags differ" in w for w in other.warnings)
    assert not rank(reports([copies["320"], copies["256"]]), P).different_masters


def test_duplicates_group_by_tags_and_duration(copies: dict[str, Path]) -> None:
    rs = reports(list(copies.values()))
    groups = find_duplicates(rs, P)
    # The remaster is 3 s longer than the rest, beyond the 2 s window; "other" has its own tags.
    assert len(groups) == 1
    names = {r.path.name for r in groups[0].members}
    assert names == {"song_320.mp3", "song_256.mp3", "song_128.mp3", "song_q6.ogg",
                     "song_fake320.mp3"}  # fmt: skip
    apply_groups(groups)
    keep = [r for r in rs if r.copy_rank == 1]
    assert len(keep) == 1 and keep[0].copy_note == groups[0].summary
    assert all(
        r.copy_group is None for r in rs if r.path.name in ("other.mp3", "song_remaster.mp3")
    )


def test_compare_command(
    copies: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["compare", str(copies["fake"]), str(copies["320"]), "--no-cache",
                 f"--csv={tmp_path / 'c.csv'}", f"--html={tmp_path / 'c.html'}"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "Copy groups:" in out and "keep song_320.mp3: authenticity" in out
    rows = {Path(r["file_path"]).name: r for r in csv.DictReader(open(tmp_path / "c.csv"))}
    assert (rows["song_320.mp3"]["copy_group"], rows["song_320.mp3"]["copy_rank"]) == ("1", "1")
    assert rows["song_fake320.mp3"]["copy_rank"] == "2"
    html = (tmp_path / "c.html").read_text()
    assert "<h2>Copy groups</h2>" in html and "keep song_320.mp3" in html


def test_compare_usage_errors(copies: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["compare", str(copies["320"])]) == 2
    assert "at least two files" in capsys.readouterr().err
    assert main(["compare", str(copies["320"]), str(copies["320"].parent)]) == 2
    assert "not folders" in capsys.readouterr().err


def test_scan_and_rescore_duplicates(
    copies: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = copies["320"].parent
    main(["scan", str(folder), "--duplicates", "--no-cache", "--json", "-o", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Copy groups:" in out and "1. Equivalent, keep any of" in out
    main(["rescore", str(tmp_path / "report.json"), "--duplicates", "--csv", "-o", str(tmp_path)])
    rows = list(csv.DictReader(open(tmp_path / "report.csv")))
    assert sum(1 for r in rows if r["copy_group"] == "1") == 5
    # Without --duplicates there are no groups.
    main(["rescore", str(tmp_path / "report.json"), "--csv", "-o", str(tmp_path)])
    assert all(r["copy_group"] == "" for r in csv.DictReader(open(tmp_path / "report.csv")))
