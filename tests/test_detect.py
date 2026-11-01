import io
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from qctrack.core.detect import Format, detect, detect_file

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()


def mp3_frames(n: int = 4, header: bytes = b"\xff\xfb\x90\x00") -> bytes:
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz, no padding: 417-byte frames.
    return b"".join(header + bytes(413) for _ in range(n))


def id3v2(payload_size: int) -> bytes:
    size = bytes(
        [
            (payload_size >> 21) & 0x7F,
            (payload_size >> 14) & 0x7F,
            (payload_size >> 7) & 0x7F,
            payload_size & 0x7F,
        ]
    )
    return b"ID3\x03\x00\x00" + size + bytes(payload_size)


def ogg_bos_page(packet: bytes, serial: int) -> bytes:
    assert len(packet) < 255
    return (
        b"OggS"
        + struct.pack("<BBqIIIB", 0, 0x02, 0, serial, 0, 0, 1)
        + bytes([len(packet)])
        + packet
    )


def d(data: bytes) -> Format:
    return detect(io.BytesIO(data)).format


def test_plain_mp3() -> None:
    assert d(mp3_frames()) is Format.MP3


def test_mp3_after_large_id3v2() -> None:
    assert d(id3v2(300_000) + mp3_frames()) is Format.MP3


def test_mp3_after_junk_bytes() -> None:
    assert d(bytes(100) + mp3_frames()) is Format.MP3


def test_single_false_sync_is_not_mp3() -> None:
    assert d(b"\xff\xfb\x90\x00" + bytes(2000)) is Format.UNKNOWN


def test_layer_ii_is_not_mp3() -> None:
    frame = b"\xff\xfd\x80\x00"  # MPEG-1 Layer II, 128 kbps, 44.1 kHz: 417 bytes
    data = b"".join(frame + bytes(413) for _ in range(3))
    assert d(data) is Format.MPEG_LAYER_1_2


def test_vorbis_ogg() -> None:
    assert d(ogg_bos_page(b"\x01vorbis" + bytes(23), 1)) is Format.VORBIS


@pytest.mark.parametrize(
    ("packet", "expected"),
    [
        (b"OpusHead" + bytes(11), Format.OPUS),
        (b"\x7fFLAC" + bytes(30), Format.FLAC),
        (b"Speex   " + bytes(60), Format.SPEEX),
        (b"\x80theora" + bytes(20), Format.OGG_OTHER),
    ],
)
def test_non_vorbis_ogg_never_reported_as_vorbis(packet: bytes, expected: Format) -> None:
    det = detect(io.BytesIO(ogg_bos_page(packet, 1)))
    assert det.format is expected
    assert not det.supported


def test_multiplexed_ogg_is_unsupported() -> None:
    data = ogg_bos_page(b"\x80theora" + bytes(20), 1) + ogg_bos_page(b"\x01vorbis" + bytes(23), 2)
    det = detect(io.BytesIO(data))
    assert det.ogg_streams == (Format.OGG_OTHER, Format.VORBIS)
    assert not det.supported


def test_native_flac_with_id3v2() -> None:
    assert d(id3v2(1000) + b"fLaC" + bytes(40)) is Format.FLAC


def test_garbage_and_empty() -> None:
    assert d(b"") is Format.UNKNOWN
    assert d(b"hello world" * 100) is Format.UNKNOWN
    assert d(b"RIFF" + bytes(100)) is Format.UNKNOWN


def test_extension_is_ignored(tmp_path: Path) -> None:
    p = tmp_path / "actually_ogg.mp3"
    p.write_bytes(ogg_bos_page(b"\x01vorbis" + bytes(23), 1))
    assert detect_file(p).format is Format.VORBIS


# --- real samples (skipped when not present) ---------------------------------------

real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")


@real
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("sample.mp3", Format.MP3),
        ("Track9.mp3", Format.MP3),
        ("sample.ogg", Format.VORBIS),
        ("sample.flac", Format.FLAC),
    ],
)
def test_real_samples(name: str, expected: Format) -> None:
    assert detect_file(SAMPLES / name).format is expected


@real
def test_cli_renamed_copy(tmp_path: Path) -> None:
    dst = tmp_path / "x.ogg"
    shutil.copy(SAMPLES / "sample.mp3", dst)
    out = subprocess.run(
        [sys.executable, "-m", "qctrack", "scan", str(dst), "--quick"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src", "PATH": ""},
    )
    assert out.returncode == 0
    assert out.stdout.strip().endswith("MP3")
