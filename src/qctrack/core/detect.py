"""Format detection from file content (F1.10, REQ-FMT-04).

The file name and extension are never consulted. Detection reads only the leading
bytes and, for Ogg, the first packet of each logical stream.
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# How far past a tag we search for the first MPEG frame, and how much Ogg header we read.
_MP3_SYNC_SEARCH = 64 * 1024
_OGG_HEAD = 64 * 1024
_MAX_ID3_TAGS = 8


class Format(enum.Enum):
    MP3 = "MP3"
    VORBIS = "Vorbis"
    OPUS = "Opus"
    FLAC = "FLAC"
    SPEEX = "Speex"
    MPEG_LAYER_1_2 = "MPEG Layer I/II"
    OGG_OTHER = "Ogg (other)"
    UNKNOWN = "Unknown"

    @property
    def supported(self) -> bool:
        """True when this release fully analyses the format (REQ-FMT-01)."""
        return self in (Format.MP3, Format.VORBIS)


@dataclass(frozen=True)
class Detection:
    format: Format
    #: Codec of every Ogg logical stream in order of appearance; empty for other containers.
    ogg_streams: tuple[Format, ...] = ()

    @property
    def supported(self) -> bool:
        if self.ogg_streams:
            return all(s is Format.VORBIS for s in self.ogg_streams)
        return self.format.supported


def detect_file(path: str | Path) -> Detection:
    """Detect the real format of ``path``. The file is opened read-only."""
    with open(path, "rb") as f:
        return detect(f)


def detect(f: BinaryIO) -> Detection:
    start = skip_id3v2(f)
    f.seek(start)
    head = f.read(4)
    if head == b"fLaC":
        return Detection(Format.FLAC)
    if head == b"OggS" and start == 0:
        return _detect_ogg(f)
    layer = _find_mpeg_audio(f, start)
    if layer == 3:
        return Detection(Format.MP3)
    if layer in (1, 2):
        return Detection(Format.MPEG_LAYER_1_2)
    return Detection(Format.UNKNOWN)


def skip_id3v2(f: BinaryIO) -> int:
    """Return the offset just past any leading ID3v2 tags."""
    pos = 0
    for _ in range(_MAX_ID3_TAGS):
        f.seek(pos)
        hdr = f.read(10)
        if len(hdr) < 10 or hdr[:3] != b"ID3":
            break
        size_bytes = hdr[6:10]
        if any(b & 0x80 for b in size_bytes):
            break  # not a valid syncsafe size
        size = (size_bytes[0] << 21) | (size_bytes[1] << 14) | (size_bytes[2] << 7) | size_bytes[3]
        footer = 10 if hdr[5] & 0x10 else 0
        pos += 10 + size + footer
    return pos


# --- MPEG audio -----------------------------------------------------------------------

BITRATES = {
    # (version, layer) -> kbps by index 1..14
    (1, 1): (32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
SAMPLE_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 25: (11025, 12000, 8000)}


def parse_mpeg_header(h: bytes) -> tuple[int, int, int] | None:
    """Parse a 4-byte MPEG audio frame header.

    Returns ``(layer, frame_length, version)`` or None if invalid. Free-format frames
    (bitrate index 0) are rejected because their length cannot be derived.
    """
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return None
    version_bits = (h[1] >> 3) & 3
    layer_bits = (h[1] >> 1) & 3
    if version_bits == 1 or layer_bits == 0:
        return None
    version = {3: 1, 2: 2, 0: 25}[version_bits]
    layer = 4 - layer_bits
    br_idx = h[2] >> 4
    sr_idx = (h[2] >> 2) & 3
    if br_idx in (0, 15) or sr_idx == 3:
        return None
    kbps = BITRATES[(1 if version == 1 else 2, layer)][br_idx - 1]
    rate = SAMPLE_RATES[version][sr_idx]
    pad = (h[2] >> 1) & 1
    if layer == 1:
        length = (12 * kbps * 1000 // rate + pad) * 4
    elif layer == 3 and version != 1:
        length = 72 * kbps * 1000 // rate + pad
    else:
        length = 144 * kbps * 1000 // rate + pad
    return layer, length, version


def _find_mpeg_audio(f: BinaryIO, start: int) -> int | None:
    """Find two consecutive valid frames and return their layer, else None."""
    f.seek(start)
    buf = f.read(_MP3_SYNC_SEARCH)
    i = 0
    while True:
        i = buf.find(b"\xff", i)
        if i < 0 or i + 4 > len(buf):
            return None
        first = parse_mpeg_header(buf[i : i + 4])
        if first is not None:
            layer, length, _ = first
            nxt = i + length
            if nxt + 4 <= len(buf):
                second = parse_mpeg_header(buf[nxt : nxt + 4])
            else:
                f.seek(start + nxt)
                second = parse_mpeg_header(f.read(4))
            if second is not None and second[0] == layer:
                return layer
        i += 1


# --- Ogg ------------------------------------------------------------------------------


def _classify_ogg_packet(pkt: bytes) -> Format:
    if pkt.startswith(b"\x01vorbis"):
        return Format.VORBIS
    if pkt.startswith(b"OpusHead"):
        return Format.OPUS
    if pkt.startswith(b"\x7fFLAC"):
        return Format.FLAC
    if pkt.startswith(b"Speex   "):
        return Format.SPEEX
    return Format.OGG_OTHER


def _detect_ogg(f: BinaryIO) -> Detection:
    f.seek(0)
    data = f.read(_OGG_HEAD)
    streams: list[Format] = []
    pos = 0
    # Beginning-of-stream pages come first, one per logical stream.
    while pos + 27 <= len(data) and data[pos : pos + 4] == b"OggS":
        _, htype, _, _, _, nseg = struct.unpack_from("<BBqIIxxxxB", data, pos + 4)
        if not htype & 0x02:
            break
        seg_end = pos + 27 + nseg
        if seg_end > len(data):
            break
        body = sum(data[pos + 27 : seg_end])
        page_end = seg_end + body
        streams.append(_classify_ogg_packet(data[seg_end : min(page_end, len(data))]))
        pos = page_end
    if not streams:
        return Detection(Format.UNKNOWN)
    # An Ogg file counts as Vorbis only if its first stream is Vorbis; any other stream
    # makes the file unsupported but never misreported as Vorbis (REQ-FMT-02).
    return Detection(streams[0], tuple(streams))
