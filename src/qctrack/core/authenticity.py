"""Derived authenticity factors for MP3: claimed vs effective quality (F3.02) and declared
vs measured bandwidth (F3.03).

Both are pure functions of stored measurements, so ``rescore`` can recompute them without
audio. The tables are the starting values from ``quality-evaluation-matrix.md`` and come
from one test clip: tune them with the labelled corpus (T10, T11).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from qctrack.core.spectrum import SpectrumResult, fade_before_cutoff


class Band(enum.IntEnum):
    """Quality band, ordered so that a larger value is better."""

    VERY_LOW = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Verdict(enum.Enum):
    PASS = "Pass"
    WARN = "Warn"
    FAIL = "Fail"


#: Lower edge of each effective band, measured cutoff in Hz (matrix, "MP3 cutoff bands").
CUTOFF_BAND_EDGES_HZ = ((19500.0, Band.HIGH), (17250.0, Band.MEDIUM), (16500.0, Band.LOW))
#: Lower edge of each claimed band, average kbps (matrix, "MP3 bitrate bands").
BITRATE_BAND_EDGES_KBPS = ((256.0, Band.HIGH), (160.0, Band.MEDIUM), (128.0, Band.LOW))
#: The bands above assume CD-class rates. Below this sample rate the cutoff is bounded by
#: Nyquist, not by the quality of the source, so F3.01 and F3.02 are not evaluated (C5).
MIN_RATE_HZ = 44100
#: A fall of at least this much just below the wall means the content had already faded:
#: the wall is likely LAME dropping faint high frequencies (sfb21), not a lower-quality
#: source, so F3.02 is at most a Warn and gate G4 does not apply (N1). Starting value from
#: test signals; tune with T10/T11.
FADE_PROTECT_DB = 12.0

#: F3.03 thresholds: how far the measured cutoff may sit below the declared lowpass.
BANDWIDTH_WARN_HZ = 1000.0
BANDWIDTH_FAIL_HZ = 2500.0


def effective_band(cutoff_hz: float) -> Band:
    """Band that a measured cutoff supports (F3.01 -> F3.02)."""
    for edge, band in CUTOFF_BAND_EDGES_HZ:
        if cutoff_hz >= edge:
            return band
    return Band.VERY_LOW


def claimed_band(average_kbps: float, lame_preset: str | None = None) -> Band:
    """Band a file claims. A LAME VBR preset sets it directly (V0-V1 High, V2-V4 Medium,
    V5 and below Low); anything else is judged by average bitrate."""
    if lame_preset and lame_preset.startswith("V") and lame_preset[1:].isdigit():
        v = int(lame_preset[1:])
        return Band.HIGH if v <= 1 else Band.MEDIUM if v <= 4 else Band.LOW
    for edge, band in BITRATE_BAND_EDGES_KBPS:
        if average_kbps >= edge:
            return band
    return Band.VERY_LOW


@dataclass(frozen=True)
class ClaimedVsEffective:
    claimed: Band
    effective: Band
    #: How many bands the content falls short of the claim (0 when it meets or beats it).
    bands_lower: int
    verdict: Verdict
    #: The fall just below the wall (dB), when there is a wall.
    fade_db: float | None = None
    #: True when a Fail was lowered to Warn because the content fades before the wall.
    protected: bool = False

    @property
    def triggers_g4(self) -> bool:
        """Gate G4: two or more bands lower, a likely transcode."""
        return self.verdict is Verdict.FAIL


def claimed_vs_effective(
    spectrum: SpectrumResult, average_kbps: float, lame_preset: str | None = None
) -> ClaimedVsEffective | None:
    """F3.02 for MP3. None when the spectrum was not measured or the rate is too low.

    Only a real cliff limits the effective band; without one the content is full band. A
    Fail becomes a Warn when the content fades before the wall (``FADE_PROTECT_DB``)."""
    if not spectrum.measured or spectrum.rate < MIN_RATE_HZ:
        return None
    claimed = claimed_band(average_kbps, lame_preset)
    effective = effective_band(spectrum.cutoff_hz) if spectrum.has_cliff else Band.HIGH
    lower = max(0, int(claimed) - int(effective))
    verdict = Verdict.PASS if lower == 0 else Verdict.WARN if lower == 1 else Verdict.FAIL
    fade = fade_before_cutoff(spectrum)
    protected = verdict is Verdict.FAIL and fade is not None and fade >= FADE_PROTECT_DB
    if protected:
        verdict = Verdict.WARN
    return ClaimedVsEffective(claimed, effective, lower, verdict, fade, protected)


@dataclass(frozen=True)
class DeclaredVsMeasured:
    declared_hz: int
    measured_hz: float
    #: Declared minus measured; negative when the content reaches beyond the declaration.
    delta_hz: float
    verdict: Verdict


def declared_vs_measured(spectrum: SpectrumResult, lowpass_hz: int) -> DeclaredVsMeasured | None:
    """F3.03 for MP3: LAME lowpass (F1.07) against the measured cutoff (F3.01).
    None when the encoder stored no lowpass or the spectrum was not measured."""
    if lowpass_hz <= 0 or not spectrum.measured:
        return None
    delta = lowpass_hz - spectrum.cutoff_hz
    verdict = (
        Verdict.PASS
        if delta < BANDWIDTH_WARN_HZ
        else Verdict.WARN
        if delta <= BANDWIDTH_FAIL_HZ
        else Verdict.FAIL
    )
    return DeclaredVsMeasured(lowpass_hz, spectrum.cutoff_hz, delta, verdict)
