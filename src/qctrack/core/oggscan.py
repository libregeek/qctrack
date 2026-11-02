"""Ogg container scanner: page CRCs, stream layout and Vorbis headers.

Covers F2.05 (page CRC), F2.14 (stream layout), the Ogg side of F2.01-F2.03 (structure,
truncation, unidentified data) and the Vorbis header fields of F1.01, F1.03-F1.05.

``libogg`` finds the pages and verifies each CRC (see ``qctrack.native.ogg``). This
module reads the page headers it returns and keeps per-stream bookkeeping.
"""

from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass, field
from pathlib import Path

from qctrack.core.detect import Format, classify_ogg_packet
from qctrack.native.ogg import OggSync

_CHUNK = 64 * 1024
_MAX_JUNK_REGIONS = 1000
_CAPTURE = b"OggS"
_CONTINUED, _BOS, _EOS = 0x01, 0x02, 0x04


@dataclass
class VorbisHeader:
    version: int
    channels: int
    rate: int
    #: Bitrate hints from the identification header in bits/s; 0 or negative mean unset.
    bitrate_max: int
    bitrate_nominal: int
    bitrate_min: int


@dataclass
class OggStream:
    """One logical bitstream."""

    serial: int
    #: Index of the chain link (group of streams that are multiplexed together).
    link: int
    codec: Format
    first_page: int
    pages: int = 0
    page_bytes: int = 0
    first_granule: int | None = None
    last_granule: int | None = None
    has_eos: bool = False
    last_pageno: int | None = None
    vorbis: VorbisHeader | None = None
    #: Vendor string from the Vorbis comment header, if it was found.
    vendor: str | None = None
    _awaiting_comment: bool = field(default=False, repr=False)

    @property
    def duration_s(self) -> float | None:
        """Length from the last granule position (Vorbis only)."""
        if self.vorbis is None or self.vorbis.rate <= 0 or self.last_granule is None:
            return None
        return max(self.last_granule, 0) / self.vorbis.rate


@dataclass
class JunkRegion:
    offset: int
    length: int


@dataclass
class OggScan:
    size: int
    pages: int = 0
    streams: list[OggStream] = field(default_factory=list)

    # F2.05: pages that start with "OggS" but fail their CRC or header checks.
    crc_failures: int = 0
    corrupt_page_bytes: int = 0
    # F2.01 / F2.03
    junk_regions: list[JunkRegion] = field(default_factory=list)
    unidentified_bytes: int = 0
    #: Page sequence numbers that jump forward: pages lost from a stream.
    lost_pages: int = 0
    #: Page sequence numbers that repeat or go backwards.
    sequence_errors: int = 0
    #: Pages of a stream whose beginning-of-stream page was never seen.
    orphan_pages: int = 0
    # F2.14
    #: Pages whose granule position is lower than the previous one in the stream.
    granule_backwards: int = 0
    #: A stream began before the earlier streams had ended.
    chain_without_eos: int = 0
    # F2.02
    #: Bytes missing from a final page that the file ends inside; 0 when none.
    truncated_bytes: int = 0

    @property
    def link_count(self) -> int:
        return len({s.link for s in self.streams})

    @property
    def chained(self) -> bool:
        return self.link_count > 1

    @property
    def multiplexed(self) -> bool:
        counts: dict[int, int] = {}
        for s in self.streams:
            counts[s.link] = counts.get(s.link, 0) + 1
        return any(n > 1 for n in counts.values())

    @property
    def missing_eos(self) -> int:
        """Streams that never ended cleanly."""
        return sum(1 for s in self.streams if not s.has_eos)

    @property
    def truncated(self) -> bool:
        return self.truncated_bytes > 0 or (bool(self.streams) and self.missing_eos > 0)

    @property
    def unidentified_fraction(self) -> float:
        return self.unidentified_bytes / self.size if self.size else 0.0

    @property
    def vorbis_streams(self) -> list[OggStream]:
        return [s for s in self.streams if s.codec is Format.VORBIS]

    @property
    def duration_s(self) -> float:
        """Total length of all Vorbis streams, from granule positions."""
        return sum(s.duration_s or 0.0 for s in self.vorbis_streams)

    @property
    def average_bitrate_kbps(self) -> float:
        """Page bytes of the Vorbis streams over their length."""
        d = self.duration_s
        return sum(s.page_bytes for s in self.vorbis_streams) * 8 / d / 1000 if d else 0.0


def scan_ogg(path: str | Path) -> OggScan:
    """Scan ``path`` (read-only) and return its Ogg measurements."""
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        r = OggScan(size=size)
        if size == 0:
            return r
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf, OggSync() as sync:
            _walk(buf, size, sync, r)
    return r


def _walk(buf: mmap.mmap, size: int, sync: OggSync, r: OggScan) -> None:
    by_serial: dict[int, OggStream] = {}
    open_serials: set[int] = set()
    link = -1
    link_has_data = False
    offset = 0
    fed = 0
    while True:
        n, header, body_len = sync.seek_page()
        if n > 0:
            serial, flags, granule, pageno = _page_fields(header)
            r.pages += 1
            stream = by_serial.get(serial)
            if flags & _BOS:
                if not open_serials or link_has_data:
                    if open_serials:
                        r.chain_without_eos += 1
                        open_serials.clear()
                    link += 1
                    link_has_data = False
                body = sync.body()
                codec = classify_ogg_packet(body)
                stream = OggStream(serial, link, codec, r.pages - 1)
                if codec is Format.VORBIS:
                    stream.vorbis = _parse_vorbis_id(body)
                    stream._awaiting_comment = True
                r.streams.append(stream)
                by_serial[serial] = stream  # a reused serial starts a new stream
                open_serials.add(serial)
            elif stream is None or serial not in open_serials:
                r.orphan_pages += 1
            else:
                link_has_data = True
            if stream is not None and (flags & _BOS or serial in open_serials):
                _update(stream, flags, granule, pageno, n, r)
                if stream._awaiting_comment and not flags & _BOS:
                    stream._awaiting_comment = False
                    if not flags & _CONTINUED:
                        stream.vendor = _parse_vendor(sync.body())
                if flags & _EOS:
                    open_serials.discard(serial)
            offset += n
        elif n < 0:
            skipped = -n
            if buf[offset : offset + 4] == _CAPTURE:
                # A page capture pattern whose CRC or header did not check out.
                r.crc_failures += 1
                r.corrupt_page_bytes += skipped
            else:
                _add_junk(r, offset, skipped)
            offset += skipped
        else:
            if fed >= size:
                break
            chunk = min(_CHUNK, size - fed)
            sync.feed(buf[fed : fed + chunk])
            fed += chunk
    _finish_tail(buf, offset, size, r)


def _page_fields(h: bytes) -> tuple[int, int, int, int]:
    flags = h[5]
    granule = struct.unpack_from("<q", h, 6)[0]
    serial, pageno = struct.unpack_from("<II", h, 14)
    return serial, flags, granule, pageno


def _update(s: OggStream, flags: int, granule: int, pageno: int, length: int, r: OggScan) -> None:
    s.pages += 1
    s.page_bytes += length
    if flags & _EOS:
        s.has_eos = True
    if s.last_pageno is not None:
        if pageno > s.last_pageno + 1:
            r.lost_pages += pageno - s.last_pageno - 1
        elif pageno <= s.last_pageno:
            r.sequence_errors += 1
    s.last_pageno = pageno
    if granule == -1:  # no packet finishes on this page
        return
    if s.last_granule is not None and granule < s.last_granule:
        r.granule_backwards += 1
    if s.first_granule is None:
        s.first_granule = granule
    s.last_granule = granule


def _add_junk(r: OggScan, offset: int, length: int) -> None:
    r.unidentified_bytes += length
    if len(r.junk_regions) < _MAX_JUNK_REGIONS:
        r.junk_regions.append(JunkRegion(offset, length))


def _finish_tail(buf: mmap.mmap, offset: int, size: int, r: OggScan) -> None:
    """Classify the bytes after the last good page: a cut-off page or junk."""
    tail = size - offset
    if tail <= 0:
        return
    head = bytes(buf[offset : offset + min(tail, 27)])
    if _CAPTURE.startswith(head[:4]) or head[:4] == _CAPTURE:
        expected = _expected_page_length(buf, offset, size)
        r.truncated_bytes += max(expected - tail, 1) if expected else 1
    else:
        _add_junk(r, offset, tail)


def _expected_page_length(buf: mmap.mmap, offset: int, size: int) -> int:
    """Total length of the page that starts at ``offset``, or 0 if its header is cut."""
    if offset + 27 > size:
        return 0
    nseg = buf[offset + 26]
    if offset + 27 + nseg > size:
        return 0
    return 27 + nseg + sum(buf[offset + 27 : offset + 27 + nseg])


def _parse_vorbis_id(body: bytes) -> VorbisHeader | None:
    if len(body) < 29 or not body.startswith(b"\x01vorbis"):
        return None
    version, channels, rate, bmax, bnom, bmin = struct.unpack_from("<IBIiii", body, 7)
    return VorbisHeader(version, channels, rate, bmax, bnom, bmin)


def _parse_vendor(body: bytes) -> str | None:
    """Vendor string from a Vorbis comment header at the start of a page body."""
    if not body.startswith(b"\x03vorbis") or len(body) < 11:
        return None
    n = struct.unpack_from("<I", body, 7)[0]
    raw = body[11 : 11 + n]
    if len(raw) < n:
        return None  # the string continues on another page
    return raw.decode("utf-8", errors="replace")
