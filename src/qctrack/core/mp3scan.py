"""In-house MP3 frame scanner and Xing/Info/LAME tag parser.

Covers F1.01-F1.08 (bitrate, mode, codec, channels, encoder, quality, lowpass, declared
vs counted length) and F2.01-F2.03 (structure, truncation, unidentified data).

The scanner walks the raw bytes once through ``mmap`` so memory stays flat for any
file size. It makes no judgement about quality: it only records measurements, and
scoring is a separate step.
"""

from __future__ import annotations

import enum
import mmap
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from qctrack.core.detect import BITRATES, SAMPLE_RATES, skip_id3v2

_CHANNEL_MODES = ("Stereo", "Joint stereo", "Dual channel", "Mono")

# LAME stores a VBR method in the low nibble of the byte after the encoder string.
_LAME_VBR_METHODS = {
    0: "Unknown",
    1: "CBR",
    2: "ABR",
    3: "VBR (old/rh)",
    4: "VBR (mtrh)",
    5: "VBR (mt)",
    6: "VBR",
    8: "CBR (2-pass)",
    9: "ABR (2-pass)",
}

# Reasons a stretch of bytes was not a frame, for reports.
_MAX_JUNK_REGIONS = 1000


class Mp3Mode(enum.Enum):
    CBR = "CBR"
    VBR = "VBR"
    ABR = "ABR"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class XingTag:
    kind: str  # "Xing", "Info" or "VBRI"
    frames: int | None
    bytes: int | None
    quality: int | None


@dataclass(frozen=True)
class LameTag:
    #: Encoder string as stored, e.g. ``LAME3.100`` or ``Lavc58.91``.
    encoder: str
    vbr_method: str
    revision: int
    #: Declared lowpass in Hz (0 when the encoder stored none).
    lowpass_hz: int
    #: Raw 11-bit preset field (0 = unknown, 8-320 = target kbps for ABR/CBR, 410-500 = VBR V9-V0).
    preset: int
    #: Nominal bitrate byte (kbps for CBR, minimum for VBR, capped at 255).
    bitrate_kbps: int
    delay_samples: int
    padding_samples: int
    #: Declared length of the audio data in bytes (0 if not stored).
    music_length: int
    music_crc: int
    #: True when the tag's own CRC-16 matches its first 190 bytes.
    tag_crc_ok: bool

    @property
    def preset_name(self) -> str | None:
        """Human name for the stored preset, or None when unknown."""
        p = self.preset
        if 8 <= p <= 320:
            return f"{p} kbps"
        if 410 <= p <= 500 and p % 10 == 0:
            return f"V{(500 - p) // 10}"
        return {
            1000: "r3mix",
            1001: "standard",
            1002: "extreme",
            1003: "insane",
            1004: "standard fast",
            1005: "extreme fast",
            1006: "medium",
            1007: "medium fast",
        }.get(p)


@dataclass
class JunkRegion:
    offset: int
    length: int


@dataclass
class Mp3Scan:
    """Everything the scanner measures about one MP3 file."""

    size: int
    #: Offset of the first frame; bytes before it are ID3v2 or junk.
    audio_start: int = 0
    #: Offset just past the last byte that belongs to audio (before trailing tags).
    audio_end: int = 0

    # F1.03 / F1.04: taken from the first audio frame.
    mpeg_version: str = ""  # "1", "2" or "2.5"
    layer: int = 0
    sample_rate: int = 0
    channel_mode: str = ""
    samples_per_frame: int = 0

    # F1.01 / F1.02
    frame_count: int = 0  # audio frames, excluding the Xing/Info/VBRI frame
    bitrate_histogram: Counter[int] = field(default_factory=Counter)
    nominal_bitrate_kbps: int = 0
    average_bitrate_kbps: float = 0.0
    mode: Mp3Mode = Mp3Mode.UNKNOWN
    channel_modes_seen: set[str] = field(default_factory=set)
    sample_rates_seen: set[int] = field(default_factory=set)

    # F1.05 - F1.07
    tag: XingTag | None = None
    lame: LameTag | None = None
    #: Encoder string if one was found anywhere in the tag frame.
    encoder: str | None = None

    # F1.08
    counted_duration_s: float = 0.0
    declared_frames: int | None = None
    declared_duration_s: float | None = None
    #: |declared - counted| / counted frames, or None when nothing was declared.
    length_mismatch: float | None = None

    # F2.01 - F2.03
    #: Number of times a valid frame was not found where one was expected.
    sync_losses: int = 0
    junk_regions: list[JunkRegion] = field(default_factory=list)
    #: Bytes inside the audio area that are not part of any frame.
    unidentified_bytes: int = 0
    #: Bytes missing from the last frame, when the file ends inside it.
    truncated_bytes: int = 0
    #: Known metadata: leading and trailing tag bytes, by kind.
    metadata_bytes: Counter[str] = field(default_factory=Counter)

    @property
    def truncated(self) -> bool:
        return self.truncated_bytes > 0

    @property
    def unidentified_fraction(self) -> float:
        return self.unidentified_bytes / self.size if self.size else 0.0

    @property
    def codec(self) -> str:
        return f"MPEG-{self.mpeg_version} Layer {'I' * self.layer if self.layer < 4 else '?'}"


# --- header decoding --------------------------------------------------------------------

#: header bytes 1..2 -> parsed fields; padding and bitrate make this at most ~10k entries.
_HeaderInfo = tuple[int, int, int, int, int, int]  # length, kbps, rate, version, layer, spf
_header_cache: dict[bytes, _HeaderInfo | None] = {}


def _decode_header(h: bytes) -> _HeaderInfo | None:
    """Decode a 4-byte header key (``h[1:3]`` plus the mode byte is not needed)."""
    # h is bytes 1..2 of the header: version/layer/protection and bitrate/rate/padding.
    b1, b2 = h[0], h[1]
    if (b1 & 0xE0) != 0xE0:
        return None
    vbits = (b1 >> 3) & 3
    lbits = (b1 >> 1) & 3
    if vbits == 1 or lbits == 0:
        return None
    version = {3: 1, 2: 2, 0: 25}[vbits]
    layer = 4 - lbits
    br_idx = b2 >> 4
    sr_idx = (b2 >> 2) & 3
    if br_idx in (0, 15) or sr_idx == 3:
        return None
    kbps = BITRATES[(1 if version == 1 else 2, layer)][br_idx - 1]
    rate = SAMPLE_RATES[version][sr_idx]
    pad = (b2 >> 1) & 1
    if layer == 1:
        length = (12 * kbps * 1000 // rate + pad) * 4
        spf = 384
    elif layer == 3 and version != 1:
        length = 72 * kbps * 1000 // rate + pad
        spf = 576
    else:
        length = 144 * kbps * 1000 // rate + pad
        spf = 1152
    return length, kbps, rate, version, layer, spf


def _header_at(buf: mmap.mmap | bytes, pos: int, end: int) -> _HeaderInfo | None:
    if pos + 4 > end or buf[pos] != 0xFF:
        return None
    key = bytes(buf[pos + 1 : pos + 3])
    try:
        return _header_cache[key]
    except KeyError:
        info = _decode_header(key)
        _header_cache[key] = info
        return info


def _compatible(a: _HeaderInfo, b: _HeaderInfo) -> bool:
    """Two frames of one stream share version, layer and sample rate."""
    return a[2] == b[2] and a[3] == b[3] and a[4] == b[4]


def _frame_ok(buf: mmap.mmap | bytes, pos: int, end: int) -> _HeaderInfo | None:
    """A frame is accepted when its header is valid and the next one lines up with it.

    The last frame (which has no successor) is accepted when it ends exactly at ``end``
    or is cut off by ``end`` (a truncated file).
    """
    h = _header_at(buf, pos, end)
    if h is None:
        return None
    nxt = pos + h[0]
    if nxt >= end - 3:
        return h
    h2 = _header_at(buf, nxt, end)
    if h2 is not None and _compatible(h, h2):
        return h
    return None


# --- trailing metadata ------------------------------------------------------------------


def _synchsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _peel_trailing_tags(buf: mmap.mmap | bytes, size: int, floor: int) -> tuple[int, Counter[str]]:
    """Walk backwards over known tags at the end of the file.

    Returns the offset where the audio area ends and the bytes taken by each tag kind.
    Never reaches below ``floor``.
    """
    end = size
    kinds: Counter[str] = Counter()
    for _ in range(8):
        # ID3v1 and its enhanced variant.
        if end - floor >= 128 and bytes(buf[end - 128 : end - 125]) == b"TAG":
            n = 128
            if end - floor >= 355 and bytes(buf[end - 355 : end - 351]) == b"TAG+":
                n = 355
            kinds["ID3v1"] += n
            end -= n
            continue
        # Lyrics3 v2 ends with a 6-digit size then "LYRICS200".
        if end - floor >= 15 and bytes(buf[end - 9 : end]) == b"LYRICS200":
            digits = bytes(buf[end - 15 : end - 9])
            if digits.isdigit():
                n = int(digits) + 15
                if n <= end - floor and bytes(buf[end - n : end - n + 11]) == b"LYRICSBEGIN":
                    kinds["Lyrics3"] += n
                    end -= n
                    continue
        # Lyrics3 v1 has no size: search back for the start marker.
        if end - floor >= 9 and bytes(buf[end - 9 : end]) == b"LYRICSEND":
            start = bytes(buf[max(floor, end - 5100) : end]).rfind(b"LYRICSBEGIN")
            if start >= 0:
                n = end - (max(floor, end - 5100) + start)
                kinds["Lyrics3"] += n
                end -= n
                continue
        # APEv1/v2 footer is 32 bytes; size counts items and footer, not the header.
        if end - floor >= 32 and bytes(buf[end - 32 : end - 24]) == b"APETAGEX":
            size_f, flags = struct.unpack_from("<II", buf, end - 20)[0:2]
            n = size_f + (32 if flags & (1 << 31) else 0)
            if 32 <= n <= end - floor:
                kinds["APE"] += n
                end -= n
                continue
        # ID3v2 appended with a footer ("3DI").
        if end - floor >= 10 and bytes(buf[end - 10 : end - 7]) == b"3DI":
            sz = bytes(buf[end - 4 : end])
            if all(b < 0x80 for b in sz):
                n = _synchsafe(sz) + 20
                if n <= end - floor:
                    kinds["ID3v2"] += n
                    end -= n
                    continue
        break
    return end, kinds


# --- Xing / Info / VBRI / LAME ----------------------------------------------------------


def _side_info_size(version: int, mono: bool) -> int:
    if version == 1:
        return 17 if mono else 32
    return 9 if mono else 17


def _parse_tag_frame(
    frame: bytes, version: int, mono: bool
) -> tuple[XingTag | None, LameTag | None, str | None]:
    """Parse the first frame if it is a Xing/Info/VBRI header frame."""
    crc = 0 if frame[1] & 1 else 2  # protection bit 0 means a 16-bit CRC follows
    off = 4 + crc + _side_info_size(version, mono)
    kind = bytes(frame[off : off + 4])
    if kind == b"VBRI" or bytes(frame[4 + crc + 32 : 4 + crc + 36]) == b"VBRI":
        o = 4 + crc + 32
        if len(frame) >= o + 18:
            nbytes, nframes = struct.unpack_from(">II", frame, o + 10)
            return XingTag("VBRI", nframes, nbytes, None), None, None
        return None, None, None
    if kind not in (b"Xing", b"Info"):
        return None, None, None
    flags = struct.unpack_from(">I", frame, off + 4)[0]
    p = off + 8
    frames = nbytes = quality = None
    if flags & 1 and len(frame) >= p + 4:
        frames = struct.unpack_from(">I", frame, p)[0]
        p += 4
    if flags & 2 and len(frame) >= p + 4:
        nbytes = struct.unpack_from(">I", frame, p)[0]
        p += 4
    if flags & 4:
        p += 100
    if flags & 8 and len(frame) >= p + 4:
        quality = struct.unpack_from(">I", frame, p)[0]
        p += 4
    tag = XingTag(kind.decode("ascii"), frames, nbytes, quality)
    # The encoder string follows directly after the Xing fields (offset 120 from "Xing"
    # when all four fields are present, which is what LAME writes).
    lame_off = off + 120
    if len(frame) < lame_off + 36:
        # Short frames may hold only the encoder string.
        enc = _ascii(frame[p : p + 9])
        return tag, None, enc or None
    s = frame[lame_off : lame_off + 9]
    encoder = _ascii(s)
    if not encoder:
        return tag, None, None
    rev_method = frame[lame_off + 9]
    lowpass = frame[lame_off + 10] * 100
    bitrate = frame[lame_off + 20]
    dp = frame[lame_off + 21 : lame_off + 24]
    delay = (dp[0] << 4) | (dp[1] >> 4)
    padding = ((dp[1] & 0x0F) << 8) | dp[2]
    preset = ((frame[lame_off + 26] << 8) | frame[lame_off + 27]) & 0x07FF
    music_length = struct.unpack_from(">I", frame, lame_off + 28)[0]
    music_crc = struct.unpack_from(">H", frame, lame_off + 32)[0]
    tag_crc = struct.unpack_from(">H", frame, lame_off + 34)[0]
    # The tag CRC covers everything in the frame up to (not including) itself, starting
    # at the frame header, using CRC-16/ARC.
    crc_ok = _crc16(bytes(frame[: lame_off + 34])) == tag_crc
    lame = LameTag(
        encoder=encoder,
        vbr_method=_LAME_VBR_METHODS.get(rev_method & 0x0F, "Unknown"),
        revision=rev_method >> 4,
        lowpass_hz=lowpass,
        preset=preset,
        bitrate_kbps=bitrate,
        delay_samples=delay,
        padding_samples=padding,
        music_length=music_length,
        music_crc=music_crc,
        tag_crc_ok=crc_ok,
    )
    return tag, lame, encoder


def _ascii(b: bytes) -> str:
    s = bytes(b).rstrip(b"\x00 ")
    if s and all(32 <= c < 127 for c in s):
        return s.decode("ascii")
    return ""


def _crc16(data: bytes) -> int:
    # LAME's tag CRC is the reflected CRC-16 with polynomial 0x8005 (CRC-16/ARC).
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


# --- scanner ----------------------------------------------------------------------------


def scan_mp3(path: str | Path) -> Mp3Scan:
    """Scan ``path`` (read-only) and return its MP3 measurements."""
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        if size == 0:
            return Mp3Scan(size=0)
        lead = skip_id3v2(f)
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
            return _scan(buf, size, lead)


def _scan(buf: mmap.mmap | bytes, size: int, lead: int) -> Mp3Scan:
    r = Mp3Scan(size=size)
    lead = min(lead, size)
    if lead:
        r.metadata_bytes["ID3v2"] += lead
    audio_end, tail_kinds = _peel_trailing_tags(buf, size, lead)
    r.metadata_bytes.update(tail_kinds)
    r.audio_end = audio_end

    pos = lead
    first = True
    prev: _HeaderInfo | None = None
    junk_start: int | None = None
    sum_kbps_frames = 0
    audio_bytes = 0
    tag_frame_bytes = 0
    while pos < audio_end:
        # An ID3v2 tag in the middle of the stream is metadata, not junk.
        if buf[pos : pos + 3] == b"ID3":
            n = _skip_inline_id3(buf, pos, audio_end)
            if n:
                _close_junk(r, junk_start, pos)
                junk_start = None
                r.metadata_bytes["ID3v2"] += n
                pos += n
                continue
        if junk_start is None and prev is not None:
            # In sync: the previous frame ended here, so a compatible header is enough.
            h = _header_at(buf, pos, audio_end)
            if h is not None and not _compatible(prev, h):
                h = None
        else:
            h = _frame_ok(buf, pos, audio_end)
        if h is None:
            if junk_start is None:
                junk_start = pos
                if not first:
                    r.sync_losses += 1
            pos += 1
            nxt = _resync(buf, pos, audio_end)
            if nxt is None:
                # Nothing valid remains. A cut-off header at the very end is truncation.
                tail = audio_end - junk_start
                if tail < 4 and _is_partial_sync(buf, junk_start, audio_end):
                    r.truncated_bytes += 4 - tail  # at least the rest of the header
                    junk_start = None
                pos = audio_end
                break
            pos = nxt
            continue
        _close_junk(r, junk_start, pos)
        junk_start = None
        prev = h
        length, kbps, rate, version, layer, spf = h
        if first:
            first = False
            r.audio_start = pos
            r.mpeg_version = {1: "1", 2: "2", 25: "2.5"}[version]
            r.layer = layer
            r.sample_rate = rate
            r.samples_per_frame = spf
            mode_bits = buf[pos + 3] >> 6
            r.channel_mode = _CHANNEL_MODES[mode_bits]
            mono = mode_bits == 3
            frame_bytes = bytes(buf[pos : min(pos + length, audio_end)])
            tag, lame, enc = (
                _parse_tag_frame(frame_bytes, version, mono)
                if layer == 3
                else (
                    None,
                    None,
                    None,
                )
            )
            if tag is not None:
                r.tag, r.lame, r.encoder = tag, lame, enc
                tag_frame_bytes = length
                pos += length
                continue
        r.channel_modes_seen.add(_CHANNEL_MODES[buf[pos + 3] >> 6])
        r.sample_rates_seen.add(rate)
        if pos + length > audio_end:
            # The file ends inside this frame.
            r.truncated_bytes += pos + length - audio_end
            r.frame_count += 1
            r.bitrate_histogram[kbps] += 1
            audio_bytes += audio_end - pos
            sum_kbps_frames += kbps
            pos = audio_end
            break
        r.frame_count += 1
        r.bitrate_histogram[kbps] += 1
        sum_kbps_frames += kbps
        audio_bytes += length
        pos += length
    _close_junk(r, junk_start, min(pos, audio_end))
    _finish(r, audio_bytes, tag_frame_bytes, sum_kbps_frames)
    return r


def _is_partial_sync(buf: mmap.mmap | bytes, pos: int, end: int) -> bool:
    return buf[pos] == 0xFF and (end - pos < 2 or (buf[pos + 1] & 0xE0) == 0xE0)


def _skip_inline_id3(buf: mmap.mmap | bytes, pos: int, end: int) -> int:
    hdr = bytes(buf[pos : pos + 10])
    if len(hdr) < 10 or any(b & 0x80 for b in hdr[6:10]) or hdr[3] == 0xFF:
        return 0
    n = 10 + _synchsafe(hdr[6:10]) + (10 if hdr[5] & 0x10 else 0)
    return n if pos + n <= end else 0


def _resync(buf: mmap.mmap | bytes, pos: int, end: int) -> int | None:
    """Find the next position where a frame, confirmed by its successor, starts."""
    while pos < end:
        i = buf.find(b"\xff", pos, end)
        if i < 0:
            return None
        if _frame_ok(buf, i, end) is not None:
            return i
        pos = i + 1
    return None


def _close_junk(r: Mp3Scan, start: int | None, end: int) -> None:
    if start is None or end <= start:
        return
    r.unidentified_bytes += end - start
    if len(r.junk_regions) < _MAX_JUNK_REGIONS:
        r.junk_regions.append(JunkRegion(start, end - start))


def _finish(r: Mp3Scan, audio_bytes: int, tag_frame_bytes: int, sum_kbps: int) -> None:
    if r.frame_count == 0:
        return
    spf, rate = r.samples_per_frame, r.sample_rate
    r.counted_duration_s = r.frame_count * spf / rate
    # F1.01: nominal is the most common frame bitrate (the CBR rate); for VBR it is only
    # the commonest value and the average is the meaningful figure.
    r.nominal_bitrate_kbps = r.bitrate_histogram.most_common(1)[0][0]
    r.average_bitrate_kbps = sum_kbps / r.frame_count
    # F1.02: bitrate mode.
    varied = len(r.bitrate_histogram) > 1
    method = r.lame.vbr_method if r.lame else ""
    if method.startswith("ABR"):
        r.mode = Mp3Mode.ABR
    elif method.startswith("VBR") or (r.tag is not None and r.tag.kind != "Info" and varied):
        r.mode = Mp3Mode.VBR
    elif varied:
        r.mode = Mp3Mode.VBR
    elif method.startswith("CBR") or (r.tag is not None and r.tag.kind == "Info"):
        r.mode = Mp3Mode.CBR
    else:
        r.mode = Mp3Mode.CBR
    # F1.08: only a stream that declares its frame count has something to compare.
    if r.tag is not None and r.tag.frames is not None:
        r.declared_frames = r.tag.frames
        r.declared_duration_s = r.tag.frames * spf / rate
        r.length_mismatch = abs(r.tag.frames - r.frame_count) / r.frame_count
