import os
import struct
import subprocess
from pathlib import Path
from typing import Any

import pytest

from qctrack.core.mp3scan import Mp3Mode, Mp3Scan, scan_mp3

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")

BR_INDEX = {32: 1, 64: 4, 128: 9, 192: 11, 256: 13, 320: 14}  # MPEG-1 Layer III


def frame(kbps: int = 128, mode: int = 1, fill: int = 0x55) -> bytes:
    """A syntactically valid 44.1 kHz MPEG-1 Layer III frame (no padding)."""
    length = 144 * kbps * 1000 // 44100
    return bytes([0xFF, 0xFB, BR_INDEX[kbps] << 4, mode << 6]) + bytes([fill]) * (length - 4)


def write(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / "t.mp3"
    p.write_bytes(data)
    return p


def id3v2(size: int) -> bytes:
    sync = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + sync + b"\x00" * size


def xing_frame(frames: int, kind: bytes = b"Xing") -> bytes:
    f = bytearray(frame(128))
    off = 4 + 32  # MPEG-1 stereo side info
    f[off : off + 4 + 4 + 4] = kind + struct.pack(">II", 1, frames)
    f[off + 120 : off + 129] = b"LAME3.100"
    f[off + 129] = 0x14  # revision 1, VBR method 4
    f[off + 130] = 205  # lowpass 20500 Hz
    f[off + 146 : off + 148] = struct.pack(">H", 410)  # V9... (preset field)
    return bytes(f)


def test_clean_cbr(tmp_path: Path) -> None:
    r = scan_mp3(write(tmp_path, frame() * 100))
    assert (r.frame_count, r.mode, r.nominal_bitrate_kbps) == (100, Mp3Mode.CBR, 128)
    assert (r.codec, r.sample_rate, r.channel_mode) == ("MPEG-1 Layer III", 44100, "Joint stereo")
    assert r.unidentified_bytes == 0 and r.sync_losses == 0 and not r.truncated
    assert r.declared_frames is None and r.length_mismatch is None
    assert r.counted_duration_s == pytest.approx(100 * 1152 / 44100)


def test_vbr_without_tag_is_vbr(tmp_path: Path) -> None:
    r = scan_mp3(write(tmp_path, frame(128) * 50 + frame(192) * 50))
    assert r.mode is Mp3Mode.VBR
    assert r.average_bitrate_kbps == pytest.approx(160)


def test_xing_and_lame_fields(tmp_path: Path) -> None:
    r = scan_mp3(write(tmp_path, xing_frame(100) + frame() * 100))
    assert r.frame_count == 100  # the tag frame is not audio
    assert r.tag is not None and r.tag.kind == "Xing"
    assert r.encoder == "LAME3.100"
    assert r.lame is not None
    assert (r.lame.lowpass_hz, r.lame.vbr_method, r.lame.preset_name) == (
        20500,
        "VBR (mtrh)",
        "V9",
    )
    assert r.declared_frames == 100 and r.length_mismatch == 0


def test_declared_length_mismatch(tmp_path: Path) -> None:
    r = scan_mp3(write(tmp_path, xing_frame(110) + frame() * 100))
    assert r.length_mismatch == pytest.approx(0.10)
    assert r.declared_duration_s == pytest.approx(110 * 1152 / 44100)


def test_info_tag_means_cbr(tmp_path: Path) -> None:
    r = scan_mp3(write(tmp_path, xing_frame(10, b"Info") + frame() * 10))
    assert r.tag is not None and r.tag.kind == "Info"


def test_known_tags_are_metadata_not_junk(tmp_path: Path) -> None:
    # Build a real APEv2 footer: version, size (items + footer), item count, flags, reserved.
    items = b"\x00" * 16
    footer = b"APETAGEX" + struct.pack("<IIII", 2000, 32 + len(items), 1, 0) + b"\x00" * 8
    lyrics_body = b"LYRICSBEGIN" + b"IND0000300" + b"LYRICSEND"
    lyrics = lyrics_body + b"%06d" % len(lyrics_body) + b"LYRICS200"
    id3v1 = b"TAG" + b"\x00" * 125
    data = id3v2(500) + frame() * 20 + items + footer + lyrics + id3v1
    r = scan_mp3(write(tmp_path, data))
    assert r.frame_count == 20
    assert r.unidentified_bytes == 0 and not r.truncated
    assert r.metadata_bytes["ID3v1"] == 128
    assert r.metadata_bytes["Lyrics3"] == len(lyrics)
    assert r.metadata_bytes["APE"] == 32 + len(items)
    assert r.metadata_bytes["ID3v2"] == 510


def test_junk_between_frames(tmp_path: Path) -> None:
    junk = b"\x00" * 300
    r = scan_mp3(write(tmp_path, frame() * 10 + junk + frame() * 10))
    assert r.frame_count == 20
    assert r.unidentified_bytes == 300
    assert r.sync_losses == 1
    assert [(j.offset, j.length) for j in r.junk_regions] == [(10 * 417, 300)]


def test_junk_appended(tmp_path: Path) -> None:
    r = scan_mp3(write(tmp_path, frame() * 10 + b"\x13" * 1000))
    assert r.frame_count == 10 and r.unidentified_bytes == 1000


def test_false_sync_inside_junk_is_not_a_frame(tmp_path: Path) -> None:
    # A lone valid-looking header that is not followed by another is ignored.
    r = scan_mp3(write(tmp_path, frame() * 10 + b"\xff\xfb\x90\x40" + b"\x00" * 50 + frame() * 5))
    assert r.frame_count == 15 and r.unidentified_bytes == 54


def test_truncated_last_frame(tmp_path: Path) -> None:
    data = frame() * 10
    r = scan_mp3(write(tmp_path, data[:-100]))
    assert r.truncated and r.truncated_bytes == 100
    assert r.unidentified_bytes == 0


def test_truncated_inside_header(tmp_path: Path) -> None:
    r = scan_mp3(write(tmp_path, frame() * 10 + b"\xff\xfb"))
    assert r.truncated and r.unidentified_bytes == 0


def test_id3v2_mid_stream_is_metadata(tmp_path: Path) -> None:
    r = scan_mp3(write(tmp_path, frame() * 5 + id3v2(100) + frame() * 5))
    assert r.frame_count == 10 and r.unidentified_bytes == 0


def test_empty_and_garbage(tmp_path: Path) -> None:
    assert scan_mp3(write(tmp_path, b"")).frame_count == 0
    r = scan_mp3(write(tmp_path, b"hello world" * 100))
    assert r.frame_count == 0 and r.unidentified_bytes == 1100


@real
@pytest.mark.parametrize(
    "name", ["sample.mp3", "Track9.mp3", "Sundara Ravanan.mp3", "01-Raghava Manohara.mp3"]
)
def test_matches_ffprobe_and_mutagen(name: str) -> None:
    from mutagen.mp3 import MP3

    path = SAMPLES / name
    if not path.exists():
        pytest.skip("sample missing")
    r = scan_mp3(path)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=sample_rate,channels",
            "-select_streams",
            "a:0",
            "-of",
            "default=nw=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    fields = dict(line.split("=") for line in probe.split())
    print(f"{name}: counted {r.counted_duration_s:.3f} s, ffprobe {fields['duration']} s")
    assert r.counted_duration_s == pytest.approx(float(fields["duration"]), abs=0.05)
    assert r.sample_rate == int(fields["sample_rate"])
    assert (r.channel_mode == "Mono") == (fields["channels"] == "1")
    info: Any = MP3(path).info
    assert r.sample_rate == info.sample_rate
    assert r.counted_duration_s == pytest.approx(info.length, abs=0.05)
    mutagen_mode = {1: "CBR", 2: "VBR", 3: "ABR"}.get(int(info.bitrate_mode))
    assert mutagen_mode is None or r.mode.value == mutagen_mode
    assert r.unidentified_bytes == 0 and r.sync_losses == 0 and not r.truncated
    if r.lame:
        assert r.lame.tag_crc_ok
        # Mutagen spells the setting "-V 0" or "-b 320"; ours is "V0" or "320 kbps".
        settings = "".join(c for c in info.encoder_settings if c.isalnum())
        name = r.lame.preset_name or ""
        assert name.replace(" kbps", "") in settings.replace("b", "", 1) or name in settings


@real
def test_truncated_real_file(tmp_path: Path) -> None:
    src = SAMPLES / "sample.mp3"
    full = scan_mp3(src)
    cut = tmp_path / "cut.mp3"
    cut.write_bytes(src.read_bytes()[: full.audio_start + 1044 * 1000 + 100])
    r = scan_mp3(cut)
    assert r.truncated and r.truncated_bytes > 0
    assert r.frame_count in (1000, 1001)  # a cut frame still counts as present
    assert 0 < r.truncated_bytes < 1045  # padding varies the frame size


@real
def test_appended_junk_real_file(tmp_path: Path) -> None:
    data = (SAMPLES / "Track9.mp3").read_bytes()
    p = tmp_path / "j.mp3"
    p.write_bytes(data + b"\x00" * 2048)
    r = scan_mp3(p)
    assert r.unidentified_bytes >= 2048 - 128  # ID3v1 is no longer at the end
    assert r.frame_count == scan_mp3(SAMPLES / "Track9.mp3").frame_count


def test_scan_type_is_exported() -> None:
    assert Mp3Scan(size=0).frame_count == 0
