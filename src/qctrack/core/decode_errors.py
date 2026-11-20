"""Decode errors (F2.06): damage the decoder concealed, which structure checks may not show.

Neither decoder reports concealed damage through an error code:

* **libmpg123** returns OK while it replaces broken frames with silence and resyncs past
  garbage. It reports both only as text (see :mod:`qctrack.native._stderr`), which
  :func:`mp3_decode_errors` parses. A resync is not damage when it is only the decoder
  running into a tag at the end of the file (ID3v1, APE, Lyrics3), so each resync is
  checked against where the scanner says the audio ends.
* **libvorbisfile** returns ``OV_HOLE`` for every gap, which the decoder counts in
  ``VorbisDecoder.holes``. :func:`vorbis_decode_errors` takes that count.

Both decoders also drop a final unit that the file ends in the middle of, without a word.
The scanners already measure that tail and F2.02 (truncation) scores it, so here it is only
recorded (``partial_tail``) and not counted: a cut-off file loses points once, not twice.

Counts are reported; Pass/Warn/Fail (0 / 1-5 / more than 5) belongs to scoring.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from qctrack.core.mp3scan import Mp3Scan
from qctrack.core.oggscan import OggScan

_BROKEN = re.compile(r"Note: broken frame (\d+)")
_ILLEGAL = re.compile(r"Illegal Audio-MPEG-Header 0x[0-9a-fA-F]+ at offset (\d+)")
_ERROR = re.compile(r"\berror:")

#: Most detail lines kept in a result.
MAX_DETAILS = 20


@dataclass
class DecodeErrors:
    """Everything the decoder concealed or dropped in one file."""

    #: Frames (MP3) or gaps (Vorbis) the decoder replaced or skipped.
    concealed: int = 0
    #: MP3: places where sync was lost that no known tag explains.
    resyncs: int = 0
    #: The file ends inside a frame or page, and the decoder dropped it. Reported, not
    #: counted: F2.02 scores it.
    partial_tail: bool = False
    #: Lost-sync notes explained by a trailing tag; reported, not counted.
    tag_notes: int = 0
    #: Set when the decoder gave up part way (the exception text); counts as one error.
    fatal: str | None = None
    #: Lines the decoder printed that were kept for the report, first ones only.
    details: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """F2.06 measured value: concealed units, unexplained resyncs and an abort."""
        return self.concealed + self.resyncs + int(self.fatal is not None)


def mp3_decode_errors(
    messages: Sequence[str], scan: Mp3Scan, fatal: str | None = None, dropped: int = 0
) -> DecodeErrors:
    """Classify libmpg123's messages against what the scanner found in the same file.

    ``dropped`` is the number of lines the capture counted but did not keep; they cannot be
    classified, so each is counted as one concealed frame (a flood of messages is damage).
    """
    r = DecodeErrors(fatal=fatal, concealed=dropped)
    # The decoder may report the offset one byte into a tag it has already started reading.
    tag_floor = scan.audio_end - 1
    for line in messages:
        if _BROKEN.search(line):
            r.concealed += 1
            _note(r, line)
            continue
        m = _ILLEGAL.search(line)
        if m:
            offset = int(m.group(1))
            if scan.audio_end > 0 and offset >= tag_floor:
                r.tag_notes += 1
            else:
                r.resyncs += 1
                _note(r, line)
            continue
        if _ERROR.search(line) or line.startswith("Warning:"):
            _note(r, line)
    r.partial_tail = scan.truncated_bytes > 0
    return r


def vorbis_decode_errors(holes: int, scan: OggScan, fatal: str | None = None) -> DecodeErrors:
    """F2.06 for Ogg Vorbis: gaps reported by libvorbisfile plus a partial final page."""
    r = DecodeErrors(concealed=holes, fatal=fatal)
    r.partial_tail = scan.truncated_bytes > 0
    if holes:
        r.details.append(f"{holes} interruption(s) reported by libvorbisfile")
    return r


def _note(r: DecodeErrors, line: str) -> None:
    if len(r.details) < MAX_DETAILS:
        r.details.append(line)
