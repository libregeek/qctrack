import os
import struct
import subprocess
from pathlib import Path
from typing import Any

import pytest

from qctrack.core.oggscan import scan_ogg

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")


def _crc_table() -> list[int]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if r & 0x80000000 else (r << 1) & 0xFFFFFFFF
        table.append(r)
    return table


_TABLE = _crc_table()


def ogg_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _TABLE[((crc >> 24) & 0xFF) ^ b]
    return crc


def page(
    serial: int, pageno: int, body: bytes, granule: int = 0, flags: int = 0, crc_ok: bool = True
) -> bytes:
    assert len(body) < 255
    h = b"OggS\x00" + bytes([flags]) + struct.pack("<qIII", granule, serial, pageno, 0)
    h += bytes([1, len(body)])
    crc = ogg_crc(h + body)
    if not crc_ok:
        crc ^= 1
    return h[:22] + struct.pack("<I", crc) + h[26:] + body


def vorbis_id(rate: int = 44100, channels: int = 2, nominal: int = 128000) -> bytes:
    return b"\x01vorbis" + struct.pack("<IBIiiiB", 0, channels, rate, 0, nominal, 0, 0xB8) + b"\x01"


def comment(vendor: bytes = b"TestEnc 1.0") -> bytes:
    return b"\x03vorbis" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", 0) + b"\x01"


def comment_packet_page(comments: list[bytes], serial: int = 7) -> bytes:
    """Page 1 of a stream, holding a Vorbis comment header with the given comments."""
    vendor = b"TestEnc 1.0"
    body = b"\x03vorbis" + struct.pack("<I", len(vendor)) + vendor
    body += struct.pack("<I", len(comments))
    for c in comments:
        body += struct.pack("<I", len(c)) + c
    return page(serial, 1, body + b"\x01", 0)


def stream(serial: int, granules: tuple[int, ...], eos: bool = True) -> bytes:
    out = page(serial, 0, vorbis_id(), 0, 0x02) + page(serial, 1, comment(), 0)
    for i, g in enumerate(granules):
        last = eos and i == len(granules) - 1
        out += page(serial, 2 + i, b"\x00" * 50, g, 0x04 if last else 0)
    return out


def write(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / "t.ogg"
    p.write_bytes(data)
    return p


def test_clean_stream(tmp_path: Path) -> None:
    r = scan_ogg(write(tmp_path, stream(7, (44100, 88200))))
    assert (r.pages, r.link_count, r.crc_failures) == (4, 1, 0)
    assert not (r.chained or r.multiplexed or r.truncated)
    s = r.streams[0]
    assert s.vendor == "TestEnc 1.0"
    assert s.vorbis is not None and (s.vorbis.rate, s.vorbis.bitrate_nominal) == (44100, 128000)
    assert s.duration_s == pytest.approx(2.0)
    assert r.unidentified_bytes == 0 and r.lost_pages == 0 and r.granule_backwards == 0


def test_chained_streams(tmp_path: Path) -> None:
    r = scan_ogg(write(tmp_path, stream(1, (44100,)) + stream(2, (88200,))))
    assert r.chained and r.link_count == 2 and not r.multiplexed
    assert r.duration_s == pytest.approx(3.0)
    assert r.chain_without_eos == 0


def test_multiplexed_streams(tmp_path: Path) -> None:
    bos = page(1, 0, vorbis_id(), 0, 0x02) + page(2, 0, b"OpusHead" + b"\x00" * 11, 0, 0x02)
    rest = page(1, 1, comment(), 0) + page(1, 2, b"\x00", 100, 0x04) + page(2, 1, b"x", 5, 0x04)
    r = scan_ogg(write(tmp_path, bos + rest))
    assert r.multiplexed and not r.chained and len(r.streams) == 2


def test_corrupt_page_counts_as_crc_failure(tmp_path: Path) -> None:
    bad = page(7, 3, b"\x00" * 50, 2000, 0, crc_ok=False)
    good = stream(7, (1000,), eos=False)  # pages 0..2
    data = good + bad + page(7, 4, b"\x00" * 50, 3000, 0x04)
    r = scan_ogg(write(tmp_path, data))
    assert r.crc_failures == 1 and r.corrupt_page_bytes == len(bad)
    assert r.lost_pages == 1  # page 3 was dropped
    assert r.unidentified_bytes == 0


def test_junk_is_unidentified(tmp_path: Path) -> None:
    s = stream(7, (1000, 2000))
    r = scan_ogg(write(tmp_path, s + b"\x00" * 500))
    assert r.unidentified_bytes == 500 and r.crc_failures == 0
    r = scan_ogg(write(tmp_path, b"\x01" * 100 + s))
    assert r.unidentified_bytes == 100 and r.pages == 4


def test_truncated_file(tmp_path: Path) -> None:
    s = stream(7, (1000, 2000))
    r = scan_ogg(write(tmp_path, s[:-20]))
    assert r.truncated and r.truncated_bytes == 20


def test_missing_eos_means_truncated(tmp_path: Path) -> None:
    r = scan_ogg(write(tmp_path, stream(7, (1000, 2000), eos=False)))
    assert r.truncated and r.missing_eos == 1 and r.truncated_bytes == 0


def test_granule_going_backwards(tmp_path: Path) -> None:
    r = scan_ogg(write(tmp_path, stream(7, (5000, 4000, 6000))))
    assert r.granule_backwards == 1


def test_orphan_pages_when_head_is_cut(tmp_path: Path) -> None:
    s = stream(7, (1000, 2000))
    first = len(page(7, 0, vorbis_id(), 0, 0x02))
    r = scan_ogg(write(tmp_path, s[first:]))
    assert r.orphan_pages == 3 and not r.streams


def test_empty_and_garbage(tmp_path: Path) -> None:
    assert scan_ogg(write(tmp_path, b"")).pages == 0
    r = scan_ogg(write(tmp_path, b"not ogg at all" * 50))
    assert r.pages == 0 and r.unidentified_bytes == 700


@real
def test_sample_matches_ffprobe_and_mutagen() -> None:
    from mutagen.oggvorbis import OggVorbis

    path = SAMPLES / "sample.ogg"
    r = scan_ogg(path)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate",
            "-of",
            "default=nw=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    fields = dict(line.split("=") for line in probe.split())
    info: Any = OggVorbis(path)  # type: ignore[no-untyped-call]
    print(
        f"counted {r.duration_s:.3f} s, ffprobe {fields['duration']} s, "
        f"{r.average_bitrate_kbps:.1f} kbps vs {int(fields['bit_rate']) / 1000:.1f}"
    )
    assert r.duration_s == pytest.approx(float(fields["duration"]), abs=0.01)
    assert r.duration_s == pytest.approx(info.info.length, abs=0.01)
    assert r.average_bitrate_kbps == pytest.approx(int(fields["bit_rate"]) / 1000, rel=0.01)
    v = r.streams[0].vorbis
    assert v is not None and (v.rate, v.channels) == (info.info.sample_rate, info.info.channels)
    assert v.bitrate_nominal == info.info.bitrate
    assert r.streams[0].vendor == info.tags.vendor
    assert (r.crc_failures, r.unidentified_bytes, r.lost_pages, r.granule_backwards) == (0, 0, 0, 0)
    assert not r.truncated and not r.chained


@real
def test_real_file_flipped_bit_and_cut(tmp_path: Path) -> None:
    data = bytearray((SAMPLES / "sample.ogg").read_bytes())
    data[len(data) // 2] ^= 0x10
    p = write(tmp_path, bytes(data))
    r = scan_ogg(p)
    assert r.crc_failures == 1 and r.lost_pages == 1
    r = scan_ogg(write(tmp_path, bytes(data[: len(data) - 1000])))
    assert r.truncated and r.crc_failures == 1
