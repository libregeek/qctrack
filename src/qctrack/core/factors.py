"""Factor scoring: turn one track's measurements into a ``FactorResult`` per factor.

This is the first half of pass 3 (the second half is ``scoring.score_track``). It is a pure
function of the measurements and the profile, so ``rescore`` can call it without audio.

A factor is handled in one of three ways:

* scored: it applies and has a measurement, so it gets a score and verdict;
* not evaluated (``score=None``): it applies, but the measurement is missing, for example
  the PCM factors in a quick scan. This lowers coverage (gate G5);
* left out: it does not apply to the codec, or this release has no measurement for it yet
  (F1.09 and the other Info factors, F2.11, F2.13, F3.05, F3.10, and F3.04 whose matrix
  entry is Info only), or the file stores nothing to judge (no declared length for F1.08,
  no LAME preset for F1.06). Left-out factors never lower coverage.

Numeric factors use the profile's curves; categorical ones follow the matrix verdicts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from qctrack.core.authenticity import (
    MIN_RATE_HZ,
    ClaimedVsEffective,
    Verdict,
    claimed_vs_effective,
    declared_vs_measured,
)
from qctrack.core.decode_errors import DecodeErrors
from qctrack.core.detect import Format
from qctrack.core.loudness import LoudnessResult
from qctrack.core.mp3scan import LameTag, Mp3Mode, Mp3Scan
from qctrack.core.oggscan import OggScan
from qctrack.core.pcm import ChannelResult, ClippingResult
from qctrack.core.profile import Curve, Profile
from qctrack.core.scoring import (
    FactorResult,
    score_piecewise,
    score_verdict,
    verdict_for_score,
)
from qctrack.core.spectrogram import Spectrogram
from qctrack.core.spectrum import SpectrumResult

_ORDER = {Verdict.PASS: 0, Verdict.WARN: 1, Verdict.FAIL: 2}
_SUPPORTED_SAMPLE_RATES = (44100, 48000)
#: Sample rates at or below this are Fail (F1.03).
_LOW_SAMPLE_RATE = 24000
#: F1.05: LAME releases before this one predate its tuned presets and get a Warn.
_OLD_LAME = (3, 90)
#: F1.05: encoder strings with a known quality problem (lowercase prefixes).
_POOR_ENCODERS = ("blade", "shine", "gogo", "xing")
#: F2.02: a declared-length shortfall is material truncation (Fail, gate G2) only when it
#: exceeds both of these. Anything smaller, such as a cut-short last frame, is a Warn.
_TRUNCATION_MIN_S = 1.0
_TRUNCATION_MIN_FRACTION = 0.01


@dataclass
class TrackMeasurements:
    """Everything measured for one supported track. Pass-2 fields stay empty or None when
    the track was only scanned quickly."""

    format: Format
    mp3: Mp3Scan | None = None
    ogg: OggScan | None = None
    decode_errors: DecodeErrors | None = None
    clipping: ClippingResult | None = None
    channels: list[ChannelResult] = field(default_factory=list)
    loudness: list[LoudnessResult] = field(default_factory=list)
    spectrum: list[SpectrumResult] = field(default_factory=list)
    #: Picture for the HTML report, only when asked for (``--spectrograms``).
    spectrogram: Spectrogram | None = None

    @property
    def duration_s(self) -> float | None:
        """Length in seconds as counted from the stream (not the declared length)."""
        if self.mp3 is not None:
            return self.mp3.counted_duration_s
        if self.ogg is not None:
            return self.ogg.duration_s
        return None


def _worst(verdicts: Iterable[Verdict]) -> Verdict:
    return max(verdicts, key=_ORDER.__getitem__)


def _numeric(factor: str, value: float, curve: Curve, detail: str) -> FactorResult:
    score = score_piecewise(value, curve)
    return FactorResult(factor, score, verdict_for_score(score), detail)


def _categorical(factor: str, verdict: Verdict, detail: str) -> FactorResult:
    return FactorResult(factor, score_verdict(verdict), verdict, detail)


def _not_evaluated(factor: str, why: str) -> FactorResult:
    return FactorResult(factor, None, None, why)


def factor_results(m: TrackMeasurements, profile: Profile) -> list[FactorResult]:
    """Factor results for an MP3 or Ogg Vorbis track, in catalogue order."""
    if m.format is Format.MP3 and m.mp3 is not None:
        out = _mp3_pass1(m.mp3, profile)
        lame = m.mp3.lame
        avg, lowpass = m.mp3.average_bitrate_kbps, lame.lowpass_hz if lame else 0
        preset = lame.preset_name if lame else None
    elif m.format is Format.VORBIS and m.ogg is not None:
        out = _vorbis_pass1(m.ogg, profile)
        avg, lowpass, preset = m.ogg.average_bitrate_kbps, 0, None
    else:
        raise ValueError(f"cannot score {m.format.value}: no measurements for a supported format")
    out += _pcm_factors(m, profile)
    out += _spectral_factors(m, profile, avg, lowpass, preset)
    return sorted(out, key=lambda r: r.factor)


# --- pass 1: headers and raw bytes --------------------------------------------------------


def _mp3_pass1(s: Mp3Scan, profile: Profile) -> list[FactorResult]:
    out = [FactorResult("F1.10", 100.0, Verdict.PASS, "MP3")]
    if s.frame_count == 0:
        return out + [_categorical("F2.01", Verdict.FAIL, "no MPEG audio frames found")]
    lame = s.lame

    # F1.01: a LAME V0-V2 preset is High whatever the average (quality matrix). A mono
    # stream needs half the bitrate of stereo, so it is judged at twice its rate.
    kbps = s.average_bitrate_kbps
    mono = s.channel_mode == "Mono"
    note = f"{kbps:.0f} kbps avg" + (f" (mono: as {2 * kbps:.0f} stereo)" if mono else "")
    v = _vbr_preset(lame) if lame else None
    if lame and v is not None and v <= 2:
        out.append(_categorical("F1.01", Verdict.PASS, f"{note}, {lame.preset_name}"))
    else:
        rate = 2 * kbps if mono else kbps
        out.append(_numeric("F1.01", rate, profile.mp3_bitrate_anchors, note))

    out.append(_sample_rate(s.sample_rate, len(s.sample_rates_seen) > 1))
    mode = s.channel_mode
    # Mono is reported (flag ``mono_content``), not scored: many masters are mono (H6).
    out.append(_categorical("F1.04", Verdict.PASS, mode or "unknown"))
    encoder = _encoder(s)
    if encoder is not None:
        out.append(encoder)
    quality = _quality_setting(s)
    if quality is not None:
        out.append(quality)
    if s.length_mismatch is not None:  # without a Xing/LAME length there is nothing to compare
        pct = s.length_mismatch * 100
        verdict = Verdict.PASS if pct < 1e-9 else Verdict.WARN if pct < 1 else Verdict.FAIL
        score = score_piecewise(pct, profile.curves["F1.08"])
        out.append(FactorResult("F1.08", score, verdict, f"{pct:.2f}% mismatch"))

    out.append(
        _numeric("F2.01", s.sync_losses, profile.curves["F2.01"], f"{s.sync_losses} sync losses")
    )
    out.append(_mp3_truncation(s))
    pct = s.unidentified_fraction * 100
    out.append(_numeric("F2.03", pct, profile.curves["F2.03"], f"{pct:.3f}% unidentified"))
    return out


def _vorbis_pass1(o: OggScan, profile: Profile) -> list[FactorResult]:
    out = [FactorResult("F1.10", 100.0, Verdict.PASS, "Vorbis")]
    streams = [s.vorbis for s in o.vorbis_streams if s.vorbis is not None]
    if streams:
        kbps = o.average_bitrate_kbps
        mono = all(v.channels == 1 for v in streams)
        note = f"{kbps:.0f} kbps avg" + (f" (mono: as {2 * kbps:.0f} stereo)" if mono else "")
        rate = 2 * kbps if mono else kbps
        out.append(_numeric("F1.01", rate, profile.vorbis_bitrate_anchors, note))
        out.append(_sample_rate(min(v.rate for v in streams), len({v.rate for v in streams}) > 1))
        if all(v.channels <= 2 for v in streams):
            counts = {v.channels for v in streams}  # mono is a flag, not a Warn (H6)
            out.append(
                _categorical(
                    "F1.04", Verdict.PASS, "/".join(str(c) for c in sorted(counts)) + " ch"
                )
            )
    structure = o.lost_pages + o.sequence_errors + o.orphan_pages
    out.append(
        _numeric("F2.01", structure, profile.curves["F2.01"], f"{structure} structure errors")
    )
    out.append(_ogg_truncation(o))
    pct = o.unidentified_fraction * 100
    out.append(_numeric("F2.03", pct, profile.curves["F2.03"], f"{pct:.3f}% unidentified"))
    out.append(
        _numeric("F2.05", o.crc_failures, profile.curves["F2.05"], f"{o.crc_failures} bad pages")
    )
    if o.granule_backwards:
        out.append(_categorical("F2.14", Verdict.FAIL, f"{o.granule_backwards} granule reversals"))
    elif o.chained:
        out.append(_categorical("F2.14", Verdict.WARN, f"chained, {o.link_count} links"))
    else:
        out.append(_categorical("F2.14", Verdict.PASS, "one stream, continuous"))
    return out


def _sample_rate(rate: int, inconsistent: bool) -> FactorResult:
    if rate in _SUPPORTED_SAMPLE_RATES:
        verdict = Verdict.PASS
    elif rate <= _LOW_SAMPLE_RATE:
        verdict = Verdict.FAIL
    else:
        verdict = Verdict.WARN
    if inconsistent:
        verdict = _worst([verdict, Verdict.WARN])
    return _categorical(
        "F1.03", verdict, f"{rate} Hz" + (", changes within file" if inconsistent else "")
    )


def _mp3_truncation(s: Mp3Scan) -> FactorResult:
    """F2.02 for MP3. Only a declared frame count shows how much audio is missing, so only
    it can justify a Fail; a last frame cut short loses at most one frame (26 ms at 44.1 kHz)
    and is common in good files."""
    missing, declared = s.missing_s, s.declared_duration_s
    if missing is not None and declared:
        if missing > max(_TRUNCATION_MIN_S, _TRUNCATION_MIN_FRACTION * declared):
            return _categorical(
                "F2.02", Verdict.FAIL, f"truncated, {missing:.1f} s of {declared:.1f} s missing"
            )
    if s.truncated_bytes > 0:
        return _categorical(
            "F2.02", Verdict.WARN, f"last frame cut short, {s.truncated_bytes} bytes missing"
        )
    if missing:
        return _categorical(
            "F2.02", Verdict.WARN, f"{s.missing_frames} declared frames missing ({missing:.2f} s)"
        )
    return _categorical("F2.02", Verdict.PASS, "complete")


def _ogg_truncation(o: OggScan) -> FactorResult:
    """F2.02 for Ogg. Ogg declares no total length, so a final page cut off in a stream that
    never ends is the only sign of an interrupted file. A missing end-of-stream flag on a
    complete final page is common and harmless."""
    if o.truncated_bytes > 0 and not o.last_ended:
        return _categorical(
            "F2.02",
            Verdict.FAIL,
            f"truncated, final page cut off ({o.truncated_bytes} bytes missing)",
        )
    if o.truncated_bytes > 0:
        return _categorical(
            "F2.02", Verdict.WARN, f"cut-off page after the end, {o.truncated_bytes} bytes"
        )
    if not o.last_ended:
        return _categorical("F2.02", Verdict.PASS, "complete, end-of-stream flag not set")
    return _categorical("F2.02", Verdict.PASS, "complete")


_LAME_VERSION = re.compile(r"^L(?:AME)?(\d+)\.(\d+)")


def _encoder(s: Mp3Scan) -> FactorResult | None:
    """F1.05. Only an identified encoder is judged (H4): FFmpeg's ``Lavc``/``Lavf`` strings
    mean its libmp3lame, i.e. LAME; old LAME and encoders with known problems are a Warn;
    anything unidentified is left out rather than penalised for being a different tool."""
    name = (s.encoder or "").strip()
    if not name:
        return None
    if name.startswith(("Lavc", "Lavf")):
        return _categorical("F1.05", Verdict.PASS, f"{name} (FFmpeg libmp3lame)")
    m = _LAME_VERSION.match(name)
    if m is not None:
        old = (int(m.group(1)), int(m.group(2))) < _OLD_LAME
        return _categorical("F1.05", Verdict.WARN if old else Verdict.PASS, name)
    if name.lower().startswith(_POOR_ENCODERS):
        return _categorical("F1.05", Verdict.WARN, name)
    return None


def _vbr_preset(lame: LameTag) -> int | None:
    """The V number of a LAME VBR preset (V0-V9), or None."""
    name = lame.preset_name
    if name and name.startswith("V") and name[1:].isdigit():
        return int(name[1:])
    return {"standard": 2, "extreme": 0}.get(name or "")


def _quality_setting(s: Mp3Scan) -> FactorResult | None:
    lame = s.lame
    if lame is None or lame.preset_name is None:
        return None  # nothing stored to judge; the factor is left out, not penalised
    name = lame.preset_name
    v = _vbr_preset(lame)
    if v is not None:
        verdict = Verdict.PASS if v <= 2 else Verdict.WARN
    elif name == "insane" or (
        224 <= lame.preset * (2 if s.channel_mode == "Mono" else 1) and s.mode is not Mp3Mode.VBR
    ):
        verdict = Verdict.PASS  # CBR or ABR at 224 kbps and above, stereo-equivalent (H4)
    else:
        verdict = Verdict.WARN  # lower CBR/ABR rates, fast presets, r3mix, medium
    return _categorical("F1.06", verdict, name)


# --- pass 2: decoded audio ----------------------------------------------------------------


def _pcm_factors(m: TrackMeasurements, profile: Profile) -> list[FactorResult]:
    out: list[FactorResult] = []
    c = profile.curves
    quick = "not analysed (quick scan)"

    if m.decode_errors is None:
        out.append(_not_evaluated("F2.06", quick))
    else:
        n = m.decode_errors.count
        out.append(_numeric("F2.06", n, c["F2.06"], f"{n} decode errors"))

    if m.clipping is None:
        out.append(_not_evaluated("F2.07", quick))
    else:
        out.append(
            _numeric(
                "F2.07",
                m.clipping.clipped_percent,
                c["F2.07"],
                f"{m.clipping.clipped_percent:.4f}% clipped",
            )
        )

    if not m.loudness:
        out += [_not_evaluated(x, quick) for x in ("F2.08", "F2.09", "F2.10")]
    else:
        peak = max(r.true_peak_db for r in m.loudness)
        out.append(_numeric("F2.08", peak, c["F2.08"], f"{peak:.1f} dBTP"))
        lufs = [r.integrated_lufs for r in m.loudness if r.integrated_lufs > -70]
        if lufs:  # silence has no integrated loudness: left out
            loud = max(lufs)
            out.append(_numeric("F2.09", loud, c["F2.09"], f"{loud:.1f} LUFS"))
        lra = min(r.lra_lu for r in m.loudness)
        out.append(_numeric("F2.10", lra, c["F2.10"], f"LRA {lra:.1f} LU"))

    if not m.channels:
        out.append(_not_evaluated("F2.12", quick))
    else:
        out.append(_channels(m.channels))
    return out


def _channels(results: list[ChannelResult]) -> FactorResult:
    verdicts: list[Verdict] = []
    notes: list[str] = []
    for r in results:
        if r.dead_channels or r.inverted:
            verdicts.append(Verdict.FAIL)
            notes.append("dead channel" if r.dead_channels else "inverted channels")
        elif r.fake_stereo:
            verdicts.append(Verdict.PASS)  # mono content in a stereo file: a flag (H6)
            notes.append("mono content (L = R)")
        else:
            verdicts.append(Verdict.PASS)
    verdict = _worst(verdicts)
    return _categorical("F2.12", verdict, "; ".join(dict.fromkeys(notes)) or "channels active")


# --- spectral factors ---------------------------------------------------------------------


def _spectral_factors(
    m: TrackMeasurements, profile: Profile, avg_kbps: float, lowpass_hz: int, preset: str | None
) -> list[FactorResult]:
    quick = "not analysed (quick scan)"
    is_mp3 = m.format is Format.MP3
    measured = [r for r in m.spectrum if r.measured]
    out: list[FactorResult] = []
    derived = ["F3.06"] + (["F3.03"] if lowpass_hz > 0 else [])
    if m.mp3 is not None and m.mp3.sample_rate >= MIN_RATE_HZ:
        derived = ["F3.01", "F3.02"] + derived
    if is_mp3 is False:
        derived = ["F3.06"]  # no Vorbis cutoff table yet (matrix: build from reference encodes)
    if not measured:
        why = quick if not m.spectrum else "too little loud audio to measure"
        return [_not_evaluated(f, why) for f in derived]

    if is_mp3:
        # F3.01 and F3.02 use CD-rate tables: below 44.1 kHz they are left out (C5), and
        # F1.03 scores the sample rate instead.
        cd_rate = [r for r in measured if r.rate >= MIN_RATE_HZ]
        if cd_rate:
            out.append(_cutoff(cd_rate, profile))
        worst = mp3_claim(m)
        if worst is not None:
            said, found = worst.claimed.name.lower(), worst.effective.name.lower()
            detail = f"claims {said}, content supports {found}"
            if worst.protected:
                detail += (
                    f"; content fades {worst.fade_db:.0f} dB before the wall, as when the "
                    "encoder drops faint highs, so not treated as a transcode"
                )
            out.append(_categorical("F3.02", worst.verdict, detail))
        if lowpass_hz > 0:
            decls = [d for r in measured if (d := declared_vs_measured(r, lowpass_hz))]
            if decls:
                d = max(decls, key=lambda d: d.delta_hz)
                out.append(
                    FactorResult(
                        "F3.03",
                        score_piecewise(d.delta_hz, profile.curves["F3.03"]),
                        d.verdict,
                        f"declared {d.declared_hz} Hz, measured {d.measured_hz:.0f} Hz",
                    )
                )
    # F3.06 is reported, not scored (weight 0 in the default profile): in a lossy file an
    # encoder lowpass near a lower rate's Nyquist looks the same as resampling (H5). It is a
    # Warn, not a Fail, so the report does not present it as a defect.
    up = [r for r in measured if r.upsampled_from_hz]
    if up:
        r = up[0]
        out.append(
            _categorical(
                "F3.06",
                Verdict.WARN,
                f"cliff at {r.cutoff_hz / 1000:.2f} kHz, as from a {r.upsampled_from_hz} Hz "
                "source or this encoder's lowpass",
            )
        )
    else:
        out.append(_categorical("F3.06", Verdict.PASS, "not detected"))
    return out


def mp3_claim(m: TrackMeasurements) -> ClaimedVsEffective | None:
    """The worst F3.02 outcome over the file's stretches (MP3 only), or None."""
    if m.mp3 is None:
        return None
    lame = m.mp3.lame
    preset = lame.preset_name if lame else None
    claims = [
        claimed_vs_effective(r, m.mp3.average_bitrate_kbps, preset)
        for r in m.spectrum
        if r.measured
    ]
    valid = [c for c in claims if c is not None]
    return max(valid, key=lambda c: (_ORDER[c.verdict], c.bands_lower)) if valid else None


def _cutoff(measured: list[SpectrumResult], profile: Profile) -> FactorResult:
    """F3.01: the lowest cutoff of any stretch. No cliff means the band is not limited."""
    cliffs = [r for r in measured if r.has_cliff]
    if not cliffs:
        return FactorResult("F3.01", 100.0, Verdict.PASS, "full band, no cutoff found")
    low = min(cliffs, key=lambda r: r.cutoff_hz)
    score = score_piecewise(low.cutoff_hz, profile.curves["F3.01"])
    return FactorResult(
        "F3.01", score, verdict_for_score(score), f"cutoff {low.cutoff_hz / 1000:.2f} kHz"
    )


__all__ = ["TrackMeasurements", "factor_results"]
