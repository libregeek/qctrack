import struct
from pathlib import Path

import pytest
from mutagen.oggvorbis import OggVorbis

from helpers import encode
from qctrack.cli.main import main
from qctrack.core.analyze import analyze_file
from qctrack.core.profile import load_default
from qctrack.core.scoring import Status

P = load_default()


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("lib")
    encode(root / "a.mp3", "-c:a", "libmp3lame", "-b:a", "320k")
    encode(root / "low.mp3", "-c:a", "libmp3lame", "-b:a", "64k")
    encode(root / "fake.mp3", "-c:a", "libmp3lame", "-b:a", "320k", broadband=False)
    encode(root / "v.ogg", "-c:a", "libvorbis", "-q:a", "6")
    (root / "notes.mp3").write_bytes(b"this is not audio at all" * 100)
    encode(root / "o.opus", "-c:a", "libopus", "-b:a", "128k")
    return root


def test_analyze_scores_a_real_mp3(library: Path) -> None:
    r = analyze_file(library / "a.mp3", P)
    assert r.score.status in (Status.SCORED, Status.PROVISIONAL)
    assert r.score.final_score is not None and r.score.rating is not None
    assert {f.factor for f in r.factors} >= {"F1.01", "F2.06", "F2.07", "F3.01", "F3.02"}
    assert all(f.score is not None for f in r.factors)


def test_analyze_scores_a_real_ogg(library: Path) -> None:
    r = analyze_file(library / "v.ogg", P)
    assert r.score.final_score is not None
    assert "F2.05" in {f.factor for f in r.factors}


def test_genuine_320_scores_a_and_a_fake_320_gets_g4(library: Path) -> None:
    real = analyze_file(library / "a.mp3", P).score
    assert (real.rating, real.gates) == ("A", ())
    fake = analyze_file(library / "fake.mp3", P).score
    # Content stops at about 12 kHz: G4, and a sound score lowered by the narrow band.
    o3 = fake.objectives["O3"]
    assert fake.gates == ("G4",) and o3 is not None and o3 < 50
    assert fake.final_score is not None and fake.final_score < 75


def _ogg_crc(page: bytes) -> int:
    crc = 0
    for byte in page:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
            crc &= 0xFFFFFFFF
    return crc


def _clear_eos(ogg: bytes) -> bytes:
    """The same file with the end-of-stream flag cleared on its last page (CRC fixed)."""
    out = bytearray(ogg)
    i = out.rfind(b"OggS")
    out[i + 5] &= 0xFF & ~0x04
    out[i + 22 : i + 26] = bytes(4)
    out[i + 22 : i + 26] = _ogg_crc(bytes(out[i:])).to_bytes(4, "little")
    return bytes(out)


def test_truncation_gates_only_material_loss(library: Path, tmp_path: Path) -> None:
    """C6: a cut-short last frame or a missing end-of-stream flag is harmless; half a file
    missing is not."""
    mp3 = (library / "a.mp3").read_bytes()
    ogg = (library / "v.ogg").read_bytes()
    cases = {
        "short_frame.mp3": (mp3[:-200], "Warn", ()),
        "half.mp3": (mp3[: len(mp3) // 2], "Fail", ("G2",)),
        "no_eos.ogg": (_clear_eos(ogg), "Pass", ()),
        "half.ogg": (ogg[: len(ogg) // 2], "Fail", ("G2",)),
    }
    for name, (data, verdict, gates) in cases.items():
        (tmp_path / name).write_bytes(data)
        r = analyze_file(tmp_path / name, P)
        f202 = next(f for f in r.factors if f.factor == "F2.02")
        assert f202.verdict is not None and f202.verdict.value == verdict, (name, f202)
        assert r.score.gates == gates, (name, r.score)
        if not gates:
            assert r.score.rating in ("A", "B"), (name, r.score)


def _ogg_page(serial: int, seq: int, flags: int, packet: bytes) -> bytes:
    head = b"OggS" + struct.pack("<BBqIII", 0, flags, 0, serial, seq, 0)
    page = bytearray(head + bytes([1, len(packet)]) + packet)
    page[22:26] = _ogg_crc(bytes(page)).to_bytes(4, "little")
    return bytes(page)


def _with_skeleton(ogg: bytes) -> bytes:
    """The same Vorbis file with an Ogg Skeleton stream: its fishead page before the Vorbis
    beginning-of-stream page and its (empty) end page right after, as the format requires."""
    fishead = b"fishead\x00" + struct.pack("<HH", 3, 0) + bytes(52)
    nseg = ogg[26]
    first_end = 27 + nseg + sum(ogg[27 : 27 + nseg])
    return (
        _ogg_page(0x5EED, 0, 0x02, fishead)
        + ogg[:first_end]
        + _ogg_page(0x5EED, 1, 0x04, b"")
        + ogg[first_end:]
    )


def test_parser_fixes_on_real_files(library: Path, tmp_path: Path) -> None:
    """7.5: a Skeleton stream, cover art in the Vorbis header, zero padding after the ID3v2
    tag and an MP4 named .mp3."""
    ogg = (library / "v.ogg").read_bytes()
    (tmp_path / "skeleton.oga").write_bytes(_with_skeleton(ogg))
    skel = analyze_file(tmp_path / "skeleton.oga", P)
    plain = analyze_file(library / "v.ogg", P)
    assert skel.score.status is Status.SCORED, skel.message
    assert skel.score.final_score == plain.score.final_score

    art = tmp_path / "art.ogg"
    art.write_bytes(ogg)
    tags = OggVorbis(art)
    tags["COMMENT"] = "x" * 1_000_000  # as large as embedded cover art
    tags.save()
    rate = {r.factor: r for r in analyze_file(art, P).factors}["F1.01"].detail
    assert rate == {r.factor: r for r in plain.factors}["F1.01"].detail

    mp3 = (library / "a.mp3").read_bytes()
    padded = tmp_path / "padded.mp3"
    tag_end = 0
    if mp3[:3] == b"ID3":  # FFmpeg writes its own tag: pad after it, as tag editors do
        size = mp3[6] << 21 | mp3[7] << 14 | mp3[8] << 7 | mp3[9]
        tag_end = 10 + size
    padded.write_bytes(mp3[:tag_end] + bytes(4096) + mp3[tag_end:])
    f203 = {r.factor: r for r in analyze_file(padded, P).factors}["F2.03"]
    assert f203.verdict is not None and f203.verdict.value == "Pass", f203
    # Zeros before the tag, too (a tag rewritten in place with leftover space).
    padded.write_bytes(_id3v2(200) + bytes(4096) + mp3)
    f203 = {r.factor: r for r in analyze_file(padded, P).factors}["F2.03"]
    assert f203.verdict is not None and f203.verdict.value == "Pass", f203

    m4a = tmp_path / "song.mp3"
    encode(m4a.with_suffix(".m4a"), "-c:a", "aac", "-b:a", "128k")
    m4a.write_bytes(m4a.with_suffix(".m4a").read_bytes())
    r = analyze_file(m4a, P)
    assert r.score.status is Status.UNSUPPORTED and r.detection is not None
    assert r.detection.format.value == "MP4/M4A"


def _id3v2(size: int) -> bytes:
    syncsafe = bytes((size >> s) & 0x7F for s in (21, 14, 7, 0))
    return b"ID3\x03\x00\x00" + syncsafe + bytes(size)


def test_unsupported_unreadable_and_missing(library: Path, tmp_path: Path) -> None:
    assert analyze_file(library / "o.opus", P).score.status is Status.UNSUPPORTED
    assert analyze_file(library / "notes.mp3", P).score.status is Status.UNSUPPORTED
    missing = analyze_file(tmp_path / "nope.mp3", P)
    assert missing.score.status is Status.ERROR and missing.message


def test_quick_scan_is_provisional_and_skips_decoding(library: Path) -> None:
    r = analyze_file(library / "a.mp3", P, quick=True)
    assert r.score.status is Status.PROVISIONAL and "G5" in r.score.gates
    assert r.measurements is not None and r.measurements.decode_errors is None
    ogg = analyze_file(library / "v.ogg", P, quick=True)
    assert ogg.score.status is Status.PROVISIONAL


def cells(line: str) -> list[str]:
    return line.split()


def test_scan_prints_an_aligned_table_and_a_summary(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", str(library)]) == 1  # the opus and the fake mp3 are Unsupported
    lines = capsys.readouterr().out.splitlines()
    header, rule, *rest = lines
    assert header.split()[:7] == ["File", "Album", "Title", "Score", "Rating", "Action", "Gates"]
    assert set(rule.replace(" ", "")) == {"-"}
    rows = rest[:6]
    assert [Path(r.split()[0]).name for r in rows] == sorted(
        ["a.mp3", "low.mp3", "fake.mp3", "v.ogg", "notes.mp3", "o.opus"]
    )
    # Columns line up: every row's score ends where the header's "Score" ends.
    end = header.index("Score") + len("Score")
    by_name = {Path(r.split()[0]).name: r for r in rows}
    assert by_name["a.mp3"][end - 4 : end].strip().replace(".", "").isdigit()
    assert "Unsupported" in by_name["o.opus"] and "Notes" in header
    assert "Opus" in by_name["o.opus"]
    assert lines[-3].startswith("6 files:") and "unsupported 2" in lines[-3]
    assert lines[-2].startswith("Actions: ") and "Replace: lossy source 1" in lines[-2]
    assert lines[-1].startswith("Gates: G1 ")


def test_scan_exit_code_is_zero_when_everything_is_analysed(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", str(library / "a.mp3"), str(library / "v.ogg")]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-2].startswith("2 files:") and "Notes" not in out
    assert out.splitlines()[-1] == "Actions: Keep 2"


def test_verbose_lists_every_factor(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["scan", "-v", str(library / "a.mp3")])
    out = capsys.readouterr().out
    assert "F3.02" in out and "objectives:" in out and "coverage 100%" in out
    assert "best copy: Best available likely" in out and "action: Keep" in out


def test_scan_uses_the_given_profile_and_rejects_a_bad_one(
    library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[factors]\n"F1.01" = -3\n')
    assert main(["scan", "--profile", str(bad), str(library / "a.mp3")]) == 2
    assert f"{bad}:2:" in capsys.readouterr().err


def test_user_profile_is_used_automatically(
    library: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = tmp_path / "cfg" / "qctrack"
    cfg.mkdir(parents=True)
    (cfg / "profile.toml").write_text('[factors]\n"F9.99" = 1\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert main(["scan", str(library / "a.mp3")]) == 2
    assert "unknown factor ID" in capsys.readouterr().err


def test_legend_follows_the_summary_and_explains_only_the_gates_shown(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["scan", str(library)])  # has Unsupported files (G1) and a band-limited 320 (G4)
    lines = capsys.readouterr().out.splitlines()
    assert lines[-3].startswith("6 files:") and lines[-2].startswith("Actions: ")
    legend = lines[-1]
    assert legend.startswith("Gates: G1 format not supported yet, not scored; G4 ")
    assert "sets the best-copy verdict" in legend and "capped" not in legend
    assert "G2" not in legend and "G3" not in legend and "G5" not in legend


def test_no_legend_when_no_gate_applies(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["scan", str(library / "a.mp3")])
    out = capsys.readouterr().out
    assert "Gates:" not in out and out.splitlines()[-2].startswith("1 files:")
    assert out.splitlines()[-1] == "Actions: Keep 1"


def test_legend_uses_the_caps_of_the_active_profile(
    library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    custom = tmp_path / "p.toml"
    custom.write_text("[gates]\nG4_cap = 45\n")
    main(["scan", "--profile", str(custom), str(library / "fake.mp3")])
    assert "capped at 45" in capsys.readouterr().out.splitlines()[-1]
