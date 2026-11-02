import os
from pathlib import Path

import pytest
from mutagen.apev2 import APEv2
from mutagen.id3 import APIC, ID3, RVA2, TALB, TIT2, TPE1, TRCK, TXXX

from qctrack.core.detect import Format
from qctrack.core.tags import parse_gain_db, read_tags

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")


def audio(n: int = 20) -> bytes:
    return (bytes([0xFF, 0xFB, 0x90, 0x40]) + b"\x00" * 413) * n


def mp3(tmp_path: Path) -> Path:
    p = tmp_path / "t.mp3"
    p.write_bytes(audio())
    return p


def test_no_tags(tmp_path: Path) -> None:
    t = read_tags(mp3(tmp_path), Format.MP3)
    assert t.formats == [] and t.title == "" and t.gains == [] and t.problems == []


def test_id3v2_fields_and_pictures(tmp_path: Path) -> None:
    p = mp3(tmp_path)
    tag = ID3()
    tag.add(TIT2(encoding=3, text="So What"))
    tag.add(TPE1(encoding=3, text="Miles Davis"))
    tag.add(TALB(encoding=3, text="Kind of Blue"))
    tag.add(TRCK(encoding=3, text="1/5"))
    tag.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=b"x" * 500))
    tag.save(p, v2_version=3)
    t = read_tags(p, Format.MP3)
    assert t.formats == ["ID3v2.3"]
    assert (t.title, t.artist, t.album, t.track_number) == (
        "So What",
        "Miles Davis",
        "Kind of Blue",
        "1",
    )
    assert (t.pictures, t.picture_bytes) == (1, 500)
    assert t.problems == []


def test_id3v1_only(tmp_path: Path) -> None:
    p = mp3(tmp_path)
    v1 = b"TAG" + b"Old Title".ljust(30, b"\0") + b"Old Artist".ljust(30, b"\0") + b"\0" * 65
    p.write_bytes(p.read_bytes() + v1)
    t = read_tags(p, Format.MP3)
    assert t.formats == ["ID3v1"] and t.title == "Old Title" and t.artist == "Old Artist"


def test_replaygain_txxx_and_rva2(tmp_path: Path) -> None:
    p = mp3(tmp_path)
    tag = ID3()
    tag.add(TXXX(encoding=3, desc="replaygain_track_gain", text="-6.50 dB"))
    tag.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK", text="0.987654"))
    tag.add(TXXX(encoding=3, desc="REPLAYGAIN_ALBUM_GAIN", text="-7.25 dB"))
    tag.add(RVA2(desc="track", channel=1, gain=-6.5, peak=0.9))
    tag.save(p)
    gains = {(g.source, g.scope): g for g in read_tags(p, Format.MP3).gains}
    track = gains[("ID3v2 TXXX", "track")]
    assert (track.gain_db, track.peak) == (-6.5, 0.987654)
    assert gains[("ID3v2 TXXX", "album")].gain_db == -7.25
    assert gains[("ID3v2 RVA2", "track")].gain_db == pytest.approx(-6.5)


def test_unparseable_gain_is_malformed(tmp_path: Path) -> None:
    p = mp3(tmp_path)
    tag = ID3()
    tag.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text="loud"))
    tag.save(p)
    t = read_tags(p, Format.MP3)
    assert any("not a number" in m for m in t.malformed)


def test_two_id3v2_tags_are_duplicates(tmp_path: Path) -> None:
    p = mp3(tmp_path)
    one = ID3()
    one.add(TIT2(encoding=3, text="A"))
    one.save(p)
    first = p.read_bytes()
    p.write_bytes(first[: first.index(audio()[:4])] + first)  # prepend a copy of the tag
    t = read_tags(p, Format.MP3)
    assert len(t.formats) == 2 and any("ID3v2 tags" in d for d in t.duplicates)


def test_duplicate_single_valued_frame(tmp_path: Path) -> None:
    p = mp3(tmp_path)
    tag = ID3()
    tag.add(TIT2(encoding=3, text=["A", "B"]))
    tag.save(p)
    assert any("TIT2" in d for d in read_tags(p, Format.MP3).duplicates)


def test_ape_tag_is_normal_on_mp3(tmp_path: Path) -> None:
    p = mp3(tmp_path)
    ape = APEv2()
    ape["Title"] = "From APE"
    ape["REPLAYGAIN_TRACK_GAIN"] = "-3.00 dB"
    ape.save(p)
    t = read_tags(p, Format.MP3)
    assert "APEv2" in t.formats and t.foreign == [] and t.problems == []
    assert [(g.source, g.gain_db) for g in t.gains] == [("APEv2", -3.0)]


def test_parse_gain_db() -> None:
    assert parse_gain_db("-6.50 dB") == -6.5
    assert parse_gain_db("+1.2dB") == 1.2
    assert parse_gain_db("n/a") is None


def _ogg_with_comments(path: Path, comments: list[bytes]) -> None:
    """Build a minimal Ogg Vorbis file carrying the given comments, via the test helper."""
    from test_oggscan import comment_packet_page, page, vorbis_id

    body = comment_packet_page(comments)
    path.write_bytes(page(7, 0, vorbis_id(), 0, 0x02) + body + page(7, 2, b"\0", 100, 0x04))


def test_vorbis_replaygain_and_duplicates(tmp_path: Path) -> None:
    p = tmp_path / "t.ogg"
    _ogg_with_comments(
        p,
        [
            b"TITLE=One",
            b"TITLE=Two",
            b"ARTIST=A",
            b"ARTIST=B",
            b"REPLAYGAIN_TRACK_GAIN=-5.10 dB",
            b"REPLAYGAIN_TRACK_PEAK=0.5",
        ],
    )
    t = read_tags(p, Format.VORBIS)
    assert t.fields["ARTIST"] == ["A", "B"]  # repeated artists are legal
    assert t.duplicates == ["TITLE has 2 values"]
    assert [(g.scope, g.gain_db, g.peak) for g in t.gains] == [("track", -5.1, 0.5)]


@real
def test_vorbis_sample_matches_mutagen_inspect() -> None:
    t = read_tags(SAMPLES / "sample.ogg", Format.VORBIS)
    assert t.formats == ["Vorbis comments"]
    assert (t.title, t.artist, t.album, t.track_number) == (
        "Thechimandaram Thulassi",
        "Vinaya",
        "Sandhyanamam Fusion",
        "2",
    )
    assert t.pictures == 1 and t.problems == [] and t.gains == []
    assert t.fields["TRACKTOTAL"] == ["10"]


@real
@pytest.mark.parametrize("name", ["sample.mp3", "Track9.mp3", "01-Raghava Manohara.mp3"])
def test_mp3_samples_match_mutagen(name: str) -> None:
    t = read_tags(SAMPLES / name, Format.MP3)
    ref = ID3(SAMPLES / name)
    assert t.title == str(ref["TIT2"]) and t.artist == " / ".join(ref["TPE1"].text)
    assert t.album == str(ref["TALB"]) and t.problems == []
    assert t.pictures == len(ref.getall("APIC"))
