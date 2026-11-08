"""Analyse one file end to end: detect, pass 1, pass 2, then score (architecture 4.1).

Pass 2 decodes the file once and streams each block to every PCM analyser, so memory stays
bounded whatever the track length. The file is only ever opened read-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from qctrack.core.decode_errors import DecodeErrors, mp3_decode_errors, vorbis_decode_errors
from qctrack.core.detect import Detection, Format, detect_file
from qctrack.core.factors import TrackMeasurements, factor_results
from qctrack.core.loudness import LoudnessAnalyzer
from qctrack.core.mp3scan import Mp3Scan, scan_mp3
from qctrack.core.oggscan import OggScan, scan_ogg
from qctrack.core.pcm import ChannelAnalyzer, ClippingAnalyzer
from qctrack.core.profile import Profile
from qctrack.core.scoring import (
    FactorResult,
    TrackScore,
    error_score,
    score_track,
    unsupported_score,
)
from qctrack.core.spectrum import SpectrumAnalyzer
from qctrack.core.tags import TagInfo, read_tags
from qctrack.native.mpg123 import Mpg123Decoder, Mpg123Error
from qctrack.native.vorbisfile import VorbisDecoder, VorbisError

Block = npt.NDArray[np.float32]

#: Raise this whenever a change to pass 1 or 2 (or to the measurement types) would give a
#: different result for the same file, so cached measurements are not reused (REQ-NFR-15).
ANALYSER_VERSION = 1


@dataclass
class Measured:
    """What pass 1 and 2 found for one file, before scoring. Plain data, so a worker
    process can send it to the parent; scoring then happens in the parent (pass 3)."""

    path: Path
    detection: Detection | None = None
    #: Why the file could not be read, when it could not.
    error: str = ""
    tags: TagInfo = field(default_factory=TagInfo)
    measurements: TrackMeasurements | None = None


@dataclass
class TrackReport:
    path: Path
    detection: Detection | None = None
    #: Why the file was not analysed (Error).
    message: str = ""
    tags: TagInfo = field(default_factory=TagInfo)
    measurements: TrackMeasurements | None = None
    factors: list[FactorResult] = field(default_factory=list)
    score: TrackScore = field(default_factory=error_score)


def measure_file(path: Path, quick: bool = False) -> Measured:
    """Passes 1 and 2 for one file. Never raises for a bad file: ``error`` is set instead."""
    out = Measured(path)
    try:
        out.detection = detect_file(path)
        fmt = out.detection.format
        if not out.detection.supported:
            return out
        out.tags = read_tags(path, fmt)
        m = TrackMeasurements(fmt)
        if fmt is Format.MP3:
            m.mp3 = scan_mp3(path)
        else:
            m.ogg = scan_ogg(path)
        if not quick:
            _decode(path, m)
        out.measurements = m
    except OSError as e:
        out.error = e.strerror or str(e)
    return out


def score_measured(measured: Measured, profile: Profile) -> TrackReport:
    """Pass 3: derived factors, gates and rating. A pure function of the measurements."""
    report = TrackReport(
        measured.path, measured.detection, measured.error, measured.tags, measured.measurements
    )
    if measured.error:
        report.score = error_score()
    elif measured.measurements is None:
        report.score = unsupported_score()
    else:
        report.factors = factor_results(measured.measurements, profile)
        report.score = score_track(report.factors, profile)
    return report


def analyze_file(path: Path, profile: Profile, quick: bool = False) -> TrackReport:
    """Measure and score one file in this process."""
    return score_measured(measure_file(path, quick), profile)


class _Sinks:
    """Every analyser that sees each decoded block."""

    def __init__(self, rate: int) -> None:
        self.clipping = ClippingAnalyzer()
        self.channels = ChannelAnalyzer()
        self.loudness = LoudnessAnalyzer(rate)
        self.spectrum = SpectrumAnalyzer(rate)
        self._rate = rate

    def feed(self, block: Block, rate: int) -> None:
        change = rate if rate != self._rate else None
        self._rate = rate
        self.clipping.feed(block)
        self.channels.feed(block)
        self.loudness.feed(block, change)
        self.spectrum.feed(block, change)


def _decode(path: Path, m: TrackMeasurements) -> None:
    if m.mp3 is not None:
        _decode_mp3(path, m, m.mp3)
    elif m.ogg is not None:
        _decode_ogg(path, m, m.ogg)


def _finish(m: TrackMeasurements, sinks: _Sinks | None, errors: DecodeErrors) -> None:
    m.decode_errors = errors
    if sinks is not None:
        m.clipping = sinks.clipping.result()
        m.channels = sinks.channels.results()
        m.loudness = sinks.loudness.results()
        m.spectrum = sinks.spectrum.results()


def _decode_mp3(path: Path, m: TrackMeasurements, scan: Mp3Scan) -> None:
    sinks: _Sinks | None = None
    fatal: str | None = None
    try:
        with Mpg123Decoder(path, capture_messages=True) as d:
            try:
                for block in d.blocks():
                    if sinks is None:
                        sinks = _Sinks(d.rate)
                    sinks.feed(block, d.rate)
            except Mpg123Error as e:
                fatal = str(e)
            errors = mp3_decode_errors(d.messages, scan, fatal, d.messages_dropped)
    except Mpg123Error as e:  # could not even open it
        errors = mp3_decode_errors([], scan, str(e))
    _finish(m, sinks, errors)


def _decode_ogg(path: Path, m: TrackMeasurements, scan: OggScan) -> None:
    sinks: _Sinks | None = None
    fatal: str | None = None
    try:
        with VorbisDecoder(path) as d:
            try:
                for block in d.blocks():
                    if sinks is None:
                        sinks = _Sinks(d.rate)
                    sinks.feed(block, d.rate)
            except VorbisError as e:
                fatal = str(e)
            errors = vorbis_decode_errors(d.holes, scan, fatal)
    except VorbisError as e:
        errors = vorbis_decode_errors(0, scan, str(e))
    _finish(m, sinks, errors)
