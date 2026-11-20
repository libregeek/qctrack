"""Best-copy verdict, flags and replacement action (requirements 5.8, 5.9, 5.11).

The sound score answers "how good does this file sound?". This module answers the other
question the user has: "should I look for a better copy, and what kind?". Like scoring, it
is a pure function of the measurements, the factor results and the profile, so ``rescore``
recomputes it without audio (REQ-ADV-02).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from qctrack.core.authenticity import VBR_HIGH_KBPS, Protection, Verdict
from qctrack.core.detect import Format
from qctrack.core.factors import TrackMeasurements, mp3_claim
from qctrack.core.mp3scan import Mp3Mode
from qctrack.core.profile import Profile
from qctrack.core.scoring import FactorResult, TrackScore
from qctrack.core.tags import TagInfo

#: Tracks shorter than this have too little audio for the spectral and dynamics factors
#: to be trusted (flag ``short_track``).
SHORT_TRACK_S = 30.0
#: Tag bytes above this are flagged as large embedded art (``cover_art_large``).
LARGE_ART_BYTES = 1_000_000
_LAME_STRINGS = ("LAME", "L3.", "Lavc", "Lavf")


class BestCopy(enum.Enum):
    UNDETERMINED = "Undetermined"
    LOSSY_SOURCE = "Lossy or band-limited source"
    LOW_BITRATE = "Low-bitrate encode"
    BEST = "Best available likely"


class Action(enum.Enum):
    """Replacement actions in priority order (requirements 5.9)."""

    REPLACE_DAMAGED = "Replace: damaged"
    REPLACE_SOURCE = "Replace: lossy source"
    REPLACE_LOW_BITRATE = "Replace: low bitrate"
    CONSIDER_MASTER = "Consider another master"
    UNDETERMINED = "Undetermined"
    KEEP_SOURCE_LIMITED = "Keep: source-limited"
    KEEP = "Keep"

    @property
    def priority(self) -> int:
        """1 (most urgent) to 7; the order of the enum."""
        return list(Action).index(self) + 1


@dataclass(frozen=True)
class Advice:
    best_copy: BestCopy
    action: Action
    #: One line that cites measured values (REQ-ADV-01).
    reason: str
    flags: tuple[str, ...] = ()


def _by_factor(factors: Sequence[FactorResult]) -> dict[str, FactorResult]:
    return {f.factor: f for f in factors}


def _failed(f: dict[str, FactorResult], factor: str) -> bool:
    return factor in f and f[factor].verdict is Verdict.FAIL


def flags_for(m: TrackMeasurements, tags: TagInfo) -> tuple[str, ...]:
    """Conditions reported in the ``flags`` column that never change the score (5.11)."""
    out: list[str] = []
    if not tags.formats:
        out.append("no_tags")
    if tags.problems:
        out.append("tag_problems")
    channels = []
    if m.mp3 is not None and m.mp3.channel_mode:
        channels.append(1 if m.mp3.channel_mode == "Mono" else 2)
    if m.ogg is not None:
        channels += [s.vorbis.channels for s in m.ogg.vorbis_streams if s.vorbis]
    if 1 in channels or any(c.fake_stereo for c in m.channels):
        out.append("mono_content")
    claim = mp3_claim(m)
    if claim is not None and claim.protection is Protection.CONTENT_ABOVE:
        out.append("band_limited_source")
    if m.format is Format.MP3:
        encoder = (m.mp3.encoder or "") if m.mp3 else ""
        if not encoder:
            out.append("unknown_encoder")
        elif not encoder.startswith(_LAME_STRINGS):
            out.append("non_lame_encoder")
    rate = (
        m.mp3.sample_rate
        if m.mp3
        else min(
            (s.vorbis.rate for s in (m.ogg.vorbis_streams if m.ogg else []) if s.vorbis), default=0
        )
    )
    if 0 < rate < 44100:
        out.append("low_sample_rate")
    duration = m.duration_s
    if duration is not None and 0 < duration < SHORT_TRACK_S:
        out.append("short_track")
    tag_bytes = tags.picture_bytes
    if m.mp3 is not None:
        tag_bytes = max(tag_bytes, sum(m.mp3.metadata_bytes.values()))
    if m.ogg is not None:
        tag_bytes = max(tag_bytes, sum(s.header_bytes for s in m.ogg.vorbis_streams))
    if tag_bytes > LARGE_ART_BYTES:
        out.append("cover_art_large")
    return tuple(out)


def encode_is_high(m: TrackMeasurements, factors: dict[str, FactorResult]) -> bool:
    """True when the encode itself leaves little to gain: F1.01's High band, a V0-V2
    preset, or a VBR average from ``VBR_HIGH_KBPS`` (stereo-equivalent, so mono counts
    double); and F1.06 not a Warn."""
    f106 = factors.get("F1.06")
    if f106 is not None and f106.verdict is Verdict.WARN:
        return False
    f101 = factors.get("F1.01")
    if f101 is None or f101.verdict is Verdict.PASS:
        return True
    s = m.mp3
    if s is not None and s.mode is Mp3Mode.VBR and (s.lame is None or s.lame.preset_name is None):
        kbps = s.average_bitrate_kbps * (2 if s.channel_mode == "Mono" else 1)
        return kbps >= VBR_HIGH_KBPS
    return False


def best_copy(m: TrackMeasurements, factors: Sequence[FactorResult], score: TrackScore) -> BestCopy:
    """Section 5.8, first match wins."""
    f = _by_factor(factors)
    if m.decode_errors is None:  # quick scan: no spectrum to judge
        return BestCopy.UNDETERMINED
    if m.spectrum and not any(r.measured for r in m.spectrum):
        return BestCopy.UNDETERMINED  # too little loud audio to measure a cutoff
    if "G4" in score.gates:
        return BestCopy.LOSSY_SOURCE
    claim = mp3_claim(m)
    if claim is not None and claim.protection is Protection.FADE:
        return BestCopy.UNDETERMINED  # a dark source or a re-encode: cannot tell
    if not encode_is_high(m, f):
        return BestCopy.LOW_BITRATE
    return BestCopy.BEST


def _damage(f: dict[str, FactorResult], score: TrackScore) -> FactorResult | None:
    """The factor that makes the file damaged (action 1), or None."""
    for factor in ("F2.02", "F2.01", "F2.04", "F2.06", "F2.12"):
        if _failed(f, factor):
            return f[factor]
    return None


def advise(
    m: TrackMeasurements,
    tags: TagInfo,
    factors: Sequence[FactorResult],
    score: TrackScore,
    profile: Profile,
) -> Advice:
    """The verdict, flags, action and reason for one scored track."""
    f = _by_factor(factors)
    flags = flags_for(m, tags)
    verdict = best_copy(m, factors, score)
    damage = _damage(f, score)
    if damage is not None:
        return Advice(verdict, Action.REPLACE_DAMAGED, f"{damage.factor}: {damage.detail}", flags)
    if verdict is BestCopy.LOSSY_SOURCE:
        cut = f.get("F3.01")
        where = f" ({cut.detail})" if cut else ""
        reason = f"{f['F3.02'].detail}{where}" if "F3.02" in f else "gate G4"
        return Advice(verdict, Action.REPLACE_SOURCE, reason, flags)
    if verdict is BestCopy.LOW_BITRATE:
        parts = [
            r.detail
            for k in ("F1.01", "F1.06")
            if (r := f.get(k)) and r.verdict is not Verdict.PASS
        ]
        return Advice(
            verdict, Action.REPLACE_LOW_BITRATE, "; ".join(parts) or "low-quality encode", flags
        )
    o4 = score.objectives.get("O4")
    limit = float(profile.advice["consider_master_below"])
    if o4 is not None and o4 < limit:
        worst = min(
            (
                r
                for k, r in f.items()
                if k in ("F2.07", "F2.08", "F2.09", "F2.10") and r.score is not None
            ),
            key=lambda r: r.score or 0.0,
        )
        return Advice(verdict, Action.CONSIDER_MASTER, f"mastering {o4:.0f}: {worst.detail}", flags)
    if verdict is BestCopy.UNDETERMINED:
        claim = mp3_claim(m)
        cut = f.get("F3.01")
        if m.decode_errors is None:
            why = "quick scan: run a full scan"
        elif claim is not None and claim.protection is Protection.FADE:
            where = f" ({cut.detail})" if cut else ""
            why = (
                f"a wall with nothing above it{where}, but the content fades "
                f"{claim.fade_db:.0f} dB before it: a dark source or a re-encode, cannot "
                "tell; if it came from a streaming or video site, a better source may exist"
            )
        else:
            why = "too little loud audio to judge"
        return Advice(verdict, Action.UNDETERMINED, why, flags)
    if "band_limited_source" in flags:
        claim = mp3_claim(m)
        share = claim.top_band_share if claim is not None else None
        return Advice(
            verdict,
            Action.KEEP_SOURCE_LIMITED,
            f"high frequencies are faint, so the encoder coded them only in {share:g}% of "
            "loud frames; unlikely to come from a lower-bitrate source",
            flags,
        )
    return Advice(verdict, Action.KEEP, "", flags)
