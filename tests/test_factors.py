from pathlib import Path

import pytest

from qctrack.core.authenticity import Verdict
from qctrack.core.decode_errors import DecodeErrors
from qctrack.core.detect import Format
from qctrack.core.factors import TrackMeasurements, factor_results
from qctrack.core.loudness import LoudnessResult
from qctrack.core.mp3scan import LameTag, Mp3Mode, Mp3Scan
from qctrack.core.oggscan import OggScan, OggStream, VorbisHeader
from qctrack.core.pcm import ChannelResult, ClippingResult
from qctrack.core.profile import load_default, load_profile
from qctrack.core.scoring import FactorResult, Status, score_track
from qctrack.core.spectrum import SpectrumResult

P = load_default()
ALL_MP3 = {
    "F1.01", "F1.03", "F1.04", "F1.05", "F1.06", "F1.10", "F2.01", "F2.02", "F2.03", "F2.06",
    "F2.07", "F2.08", "F2.09", "F2.10", "F2.12", "F3.01", "F3.02", "F3.03", "F3.06",
}  # fmt: skip


def lame(preset: int, encoder: str = "LAME3.100", lowpass: int = 20500) -> LameTag:
    # preset field: 8-320 = kbps, 410 = V9 ... 500 = V0, 1003 = insane
    return LameTag(encoder, "VBR", 1, lowpass, preset, 0, 0, 0, 0, 0, 0.0, None, None, True)


def mp3(**kw: object) -> Mp3Scan:
    base: dict[str, object] = {
        "size": 4_000_000, "frame_count": 1000, "sample_rate": 44100,
        "channel_mode": "Joint stereo", "average_bitrate_kbps": 320.0, "mode": Mp3Mode.CBR,
        "samples_per_frame": 1152,
        "sample_rates_seen": {44100}, "encoder": "LAME3.100", "lame": lame(320),
    }  # fmt: skip
    return Mp3Scan(**{**base, **kw})  # type: ignore[arg-type]


def spectrum(cutoff: float = 20500.0, cliff: bool = True, rate: int = 44100) -> SpectrumResult:
    return SpectrumResult(
        rate=rate, frames=100, loud_frames=100, measured=True, nyquist_hz=rate / 2,
        cutoff_hz=cutoff, has_cliff=cliff,
    )  # fmt: skip


def pcm(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "decode_errors": DecodeErrors(),
        "clipping": ClippingResult(samples=1_000_000),
        "channels": [ChannelResult(2, 1000, [-20.0, -20.0], correlation=0.5)],
        "loudness": [LoudnessResult(2, 44100, 1000, -3.0, -2.0, -14.0, 8.0)],
        "spectrum": [spectrum()],
    }
    return {**base, **kw}


def run(scan: Mp3Scan, **kw: object) -> dict[str, FactorResult]:
    m = TrackMeasurements(Format.MP3, mp3=scan, **pcm(**kw))  # type: ignore[arg-type]
    return {r.factor: r for r in factor_results(m, P)}


def test_clean_320_mp3_scores_a_hundred() -> None:
    results = run(mp3())
    assert set(results) == ALL_MP3
    assert all(r.score == 100.0 for r in results.values())
    s = score_track(results.values(), P)
    assert (s.final_score, s.rating, s.status, s.gates) == (100.0, "A", Status.SCORED, ())


def test_fake_320_with_low_cutoff_gets_g4() -> None:
    results = run(mp3(), spectrum=[spectrum(cutoff=16000.0)])
    assert results["F3.02"].verdict is Verdict.FAIL
    assert results["F3.01"].verdict is Verdict.FAIL
    s = score_track(results.values(), P)
    # Profile 2.0: G4 sets the best-copy verdict; the narrow band lowers the score via F3.01.
    f301 = results["F3.01"].score
    assert s.gates == ("G4",) and f301 is not None and s.objectives["O3"] == round(f301, 1)
    assert s.final_score is not None and s.final_score > 59  # no cap


def test_loudness_scored_only_at_the_loud_extreme() -> None:
    loud = LoudnessResult(2, 44100, 1000, -0.5, -0.2, -6.0, 4.0)
    quiet = LoudnessResult(2, 44100, 1000, -12.0, -11.5, -28.0, 12.0)
    assert run(mp3(), loudness=[loud])["F2.09"].score == 25.0
    assert run(mp3(), loudness=[quiet])["F2.09"].score == 100.0
    silent = LoudnessResult(2, 44100, 1000, -float("inf"), -float("inf"), -float("inf"), 0.0)
    assert "F2.09" not in run(mp3(), loudness=[silent])


def declared(frames: int) -> dict[str, object]:
    """Xing/Info header fields for a file declaring ``frames`` frames of 1152 at 44.1 kHz."""
    return {"declared_frames": frames, "declared_duration_s": frames * 1152 / 44100}


def test_truncated_mp3_gets_g2() -> None:
    # 1000 of 2000 declared frames present: 26 s of 52 s missing.
    results = run(mp3(truncated_bytes=500, **declared(2000)))
    assert results["F2.02"].verdict is Verdict.FAIL
    assert results["F2.02"].detail == "truncated, 26.1 s of 52.2 s missing"
    s = score_track(results.values(), P)
    assert (s.gates, s.rating, s.final_score) == (("G2",), "F", 39.0)


def test_cut_short_last_frame_is_a_warn_without_gate() -> None:
    # The file ends inside its last frame, which the declared count includes (C6).
    for kw in ({}, declared(1000)):
        results = run(mp3(truncated_bytes=500, **kw))
        assert results["F2.02"].verdict is Verdict.WARN, kw
        s = score_track(results.values(), P)
        assert s.gates == () and s.rating == "A", (kw, s)


def test_small_declared_shortfall_is_a_warn() -> None:
    # 20 frames (0.5 s) missing is below max(1 s, 1%): Warn, no gate.
    assert run(mp3(**declared(1020)))["F2.02"].verdict is Verdict.WARN
    # 40 frames (1.04 s) of 26.6 s declared is above both limits: Fail.
    assert run(mp3(**declared(1040)))["F2.02"].verdict is Verdict.FAIL
    # The 1% limit applies to long tracks: 0.04% of a long declared length is a Warn.
    long = {"frame_count": 100_000, **declared(100_040)}
    assert run(mp3(**long))["F2.02"].verdict is Verdict.WARN


def test_32khz_leaves_out_cutoff_factors() -> None:
    """C5: at 32 kHz the cutoff is bounded by Nyquist, so F3.01 and F3.02 are left out and
    gate G4 cannot fire; F1.03 scores the sample rate."""
    results = run(
        mp3(sample_rate=32000, sample_rates_seen={32000}, average_bitrate_kbps=160.0),
        spectrum=[spectrum(cutoff=15400.0, rate=32000)],
    )
    assert "F3.01" not in results and "F3.02" not in results
    assert results["F1.03"].verdict is Verdict.WARN
    assert "G4" not in score_track(results.values(), P).gates


def test_sync_losses_and_unidentified_data() -> None:
    assert run(mp3(sync_losses=2))["F2.01"].verdict is Verdict.WARN
    assert run(mp3(sync_losses=4))["F2.01"].verdict is Verdict.FAIL
    assert run(mp3(unidentified_bytes=2000))["F2.03"].verdict is Verdict.WARN  # 0.05%
    assert run(mp3(unidentified_bytes=40_000))["F2.03"].verdict is Verdict.FAIL  # 1%


def test_bitrate_bands_without_a_lame_preset() -> None:
    kw = {"lame": None, "encoder": None}
    assert run(mp3(average_bitrate_kbps=192.0, **kw))["F1.01"].score == pytest.approx(
        75 + 25 * 32 / 96
    )
    low = run(mp3(average_bitrate_kbps=96.0, **kw))["F1.01"]
    assert (low.score, low.verdict) == (25.0, Verdict.FAIL)
    assert run(mp3(average_bitrate_kbps=140.0, **kw))["F1.01"].verdict is Verdict.WARN


def test_lame_v0_to_v2_is_high_whatever_the_average() -> None:
    v1 = run(mp3(average_bitrate_kbps=190.0, lame=lame(490)))["F1.01"]
    assert (v1.score, v1.verdict) == (100.0, Verdict.PASS)
    v7 = run(mp3(average_bitrate_kbps=190.0, lame=lame(430)))["F1.01"]
    assert v7.verdict is Verdict.WARN


def f105(encoder: str | None) -> FactorResult | None:
    return run(mp3(encoder=encoder, lame=None)).get("F1.05")


def test_encoder_identity() -> None:
    """H4: identified encoders are judged; unidentified ones are left out, not penalised."""
    for name in ("LAME3.100", "LAME3.98.4", "LAME3.97", "LAME3.90", "L3.99r", "Lavc63.1."):
        r = f105(name)
        assert r is not None and r.verdict is Verdict.PASS, name
    lavc = f105("Lavc58.91")
    assert lavc is not None and lavc.detail == "Lavc58.91 (FFmpeg libmp3lame)"
    for name in ("LAME3.89", "LAME3.70", "Xing", "BladeEnc", "Shine", "GOGO3.13"):
        r = f105(name)
        assert r is not None and r.verdict is Verdict.WARN, name
    assert f105(None) is None and f105("") is None and f105("iTunes") is None
    # Leaving F1.05 out does not lower coverage.
    assert score_track(run(mp3(encoder=None, lame=None)).values(), P).coverage_pct == 100.0


def test_encoder_preset() -> None:
    assert run(mp3(lame=lame(450)))["F1.06"].verdict is Verdict.WARN  # V5
    assert run(mp3(lame=lame(490)))["F1.06"].verdict is Verdict.PASS  # V1
    assert run(mp3(lame=lame(1003)))["F1.06"].verdict is Verdict.PASS  # insane
    assert run(mp3(lame=lame(192)))["F1.06"].verdict is Verdict.WARN  # ABR/CBR 192
    for kbps in (224, 256, 320):  # H4: CBR and ABR from 224 kbps up are high quality
        assert run(mp3(lame=lame(kbps)))["F1.06"].verdict is Verdict.PASS, kbps
    vbr = run(mp3(lame=lame(256), mode=Mp3Mode.VBR))["F1.06"]
    assert vbr.verdict is Verdict.WARN  # a kbps preset on a VBR stream is not a CBR claim


def test_factors_with_nothing_stored_are_left_out_not_penalised() -> None:
    results = run(mp3(lame=None, encoder=None))
    assert not {"F1.05", "F1.06", "F1.08", "F3.03"} & set(results)
    s = score_track(results.values(), P)
    assert s.coverage_pct == 100.0


def test_sample_rate_channels_and_length() -> None:
    assert run(mp3(sample_rate=32000))["F1.03"].verdict is Verdict.WARN
    assert run(mp3(sample_rate=22050))["F1.03"].verdict is Verdict.FAIL
    assert run(mp3(sample_rates_seen={44100, 48000}))["F1.03"].verdict is Verdict.WARN
    assert run(mp3(channel_mode="Mono"))["F1.04"].verdict is Verdict.PASS  # a flag (H6)
    assert run(mp3(length_mismatch=0.0))["F1.08"].verdict is Verdict.PASS
    assert run(mp3(length_mismatch=0.004))["F1.08"].verdict is Verdict.WARN
    assert run(mp3(length_mismatch=0.01))["F1.08"].verdict is Verdict.FAIL


def test_pcm_factors() -> None:
    r = run(
        mp3(),
        decode_errors=DecodeErrors(concealed=3),
        clipping=ClippingResult(samples=1000, clipped_samples=1),  # 0.1%
        loudness=[LoudnessResult(2, 44100, 1, 0.0, 0.5, -8.0, 2.0)],
    )
    assert r["F2.06"].verdict is Verdict.WARN
    assert r["F2.07"].verdict is Verdict.WARN
    assert r["F2.08"].verdict is Verdict.WARN and r["F2.08"].score == 50.0
    assert r["F2.10"].verdict is Verdict.WARN  # PLR 0.5 - (-8.0) = 8.5 dB
    assert r["F2.10"].detail == "PLR 8.5 dB (LRA 2.0 LU)"
    assert run(mp3(), decode_errors=DecodeErrors(concealed=9))["F2.06"].verdict is Verdict.FAIL


def test_dynamics_from_peak_to_loudness_ratio() -> None:
    """H7: a steady track (loudness range 0) is not a failure; a crushed master is."""
    steady = LoudnessResult(2, 44100, 1, -3.0, -2.9, -11.6, 0.0)  # PLR 8.7
    crushed = LoudnessResult(2, 44100, 1, -0.1, 0.4, -4.1, 6.0)  # PLR 4.5
    assert run(mp3(), loudness=[steady])["F2.10"].score == pytest.approx(92.5)
    assert run(mp3(), loudness=[crushed])["F2.10"].verdict is Verdict.FAIL


@pytest.mark.parametrize(
    ("result", "verdict"),
    [
        (ChannelResult(2, 10, [-20.0, -20.0], correlation=0.4), Verdict.PASS),
        (ChannelResult(2, 10, [-20.0, -20.0], correlation=1.0), Verdict.PASS),  # flag (H6)
        (ChannelResult(2, 10, [-20.0, -20.0], correlation=-0.9), Verdict.FAIL),
        (ChannelResult(2, 10, [-20.0, -90.0], correlation=0.1, dead_channels=[1]), Verdict.FAIL),
        (ChannelResult(1, 10, [-20.0]), Verdict.PASS),
    ],
)
def test_channel_anomalies(result: ChannelResult, verdict: Verdict) -> None:
    assert run(mp3(), channels=[result])["F2.12"].verdict is verdict


def test_cutoff_scores_and_no_cliff() -> None:
    assert run(mp3(), spectrum=[spectrum(18000.0)])["F3.01"].score == pytest.approx(75.0)
    full = run(mp3(), spectrum=[spectrum(22050.0, cliff=False)])
    assert full["F3.01"].score == 100.0


def test_declared_lowpass_far_above_measured_cutoff() -> None:
    r = run(mp3(lame=lame(320, lowpass=20500)), spectrum=[spectrum(17000.0)])["F3.03"]
    assert r.verdict is Verdict.FAIL  # 3.5 kHz below the declaration
    assert 0 < (r.score or 0) < 50


def test_upsampling_evidence_is_reported_but_not_scored() -> None:
    """H5: a cliff near 16 kHz may be a 32 kHz source or the encoder's own lowpass."""
    up = spectrum(cutoff=15500.0)
    up.upsampled_from_hz = 32000
    results = run(mp3(average_bitrate_kbps=112.0, lame=lame(112)), spectrum=[up])
    assert results["F3.06"].verdict is Verdict.WARN
    assert "32000 Hz source or this encoder's lowpass" in results["F3.06"].detail
    assert P.factors["F3.06"] == 0
    clean = run(mp3(average_bitrate_kbps=112.0, lame=lame(112)), spectrum=[spectrum(15500.0)])
    s, c = score_track(results.values(), P), score_track(clean.values(), P)
    assert s.final_score == c.final_score


def test_vorbis_o3_drops_out_without_a_scored_factor() -> None:
    """N2: Vorbis has no cutoff table yet, so O3 has no weighted factor and is renormalised
    away instead of resting on F3.06 alone."""
    up = spectrum(cutoff=15500.0)
    up.upsampled_from_hz = 32000
    s = score_track(run_ogg(vorbis(), spectrum=[up]).values(), P)
    assert s.objectives["O3"] is None and s.rating == "A"


def test_quick_scan_leaves_pass_2_factors_not_evaluated_and_is_provisional() -> None:
    m = TrackMeasurements(Format.MP3, mp3=mp3())
    results = {r.factor: r for r in factor_results(m, P)}
    for f in ("F2.06", "F2.07", "F2.08", "F2.10", "F2.12", "F3.01", "F3.02", "F3.06"):
        assert f in results and results[f].score is None
    s = score_track(results.values(), P)
    assert s.status is Status.PROVISIONAL and s.coverage_pct < 70 and "G5" in s.gates


def test_unmeasured_spectrum_is_not_evaluated() -> None:
    quiet = SpectrumResult(rate=44100)
    r = run(mp3(), spectrum=[quiet])
    assert r["F3.01"].score is None and r["F3.02"].score is None


def test_scores_depend_on_the_profile(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text('[curves]\n"F3.01" = [[0, 0], [22000, 100]]\n')
    custom = load_profile(path)
    m = TrackMeasurements(Format.MP3, mp3=mp3(), **pcm(spectrum=[spectrum(11000.0)]))  # type: ignore[arg-type]
    by = {r.factor: r for r in factor_results(m, custom)}
    assert by["F3.01"].score == pytest.approx(50.0)


# --- Ogg Vorbis ---------------------------------------------------------------------------


def vorbis(has_eos: bool = True, **kw: object) -> OggScan:
    hdr = VorbisHeader(
        version=0, channels=2, rate=44100, bitrate_max=0, bitrate_nominal=192000, bitrate_min=0
    )
    stream = OggStream(
        serial=1, link=0, codec=Format.VORBIS, first_page=0, pages=100, page_bytes=1_000_000,
        first_granule=0, last_granule=44100 * 40, has_eos=has_eos, vorbis=hdr,
    )  # fmt: skip
    return OggScan(size=1_000_500, streams=[stream], **kw)  # type: ignore[arg-type]


def run_ogg(scan: OggScan, **kw: object) -> dict[str, FactorResult]:
    m = TrackMeasurements(Format.VORBIS, ogg=scan, **pcm(**kw))  # type: ignore[arg-type]
    return {r.factor: r for r in factor_results(m, P)}


def test_clean_vorbis_scores_and_leaves_out_mp3_only_factors() -> None:
    results = run_ogg(vorbis())
    assert set(results) == {
        "F1.01", "F1.03", "F1.04", "F1.10", "F2.01", "F2.02", "F2.03", "F2.05", "F2.06",
        "F2.07", "F2.08", "F2.09", "F2.10", "F2.12", "F2.14", "F3.06",
    }  # fmt: skip
    assert all(r.score == 100.0 for r in results.values()), {
        f: (r.score, r.detail) for f, r in results.items() if r.score != 100.0
    }


def test_vorbis_bitrate_and_integrity() -> None:
    assert run_ogg(vorbis())["F1.01"].detail == "200 kbps avg"
    assert run_ogg(vorbis(crc_failures=2))["F2.05"].verdict is Verdict.FAIL
    assert run_ogg(vorbis(lost_pages=1))["F2.01"].verdict is Verdict.WARN
    assert run_ogg(vorbis(granule_backwards=1))["F2.14"].verdict is Verdict.FAIL
    # A cut-off final page: the stream never reached its end-of-stream page.
    assert run_ogg(vorbis(truncated_bytes=10, has_eos=False))["F2.02"].verdict is Verdict.FAIL


def test_vorbis_end_of_stream_flag() -> None:
    # A complete final page without the end-of-stream flag is not truncation (C6).
    open_ended = run_ogg(vorbis(has_eos=False))["F2.02"]
    assert open_ended.verdict is Verdict.PASS
    assert open_ended.detail == "complete, end-of-stream flag not set"
    # A cut-off page after a stream that did end is a Warn without gate.
    assert run_ogg(vorbis(truncated_bytes=10, has_eos=True))["F2.02"].verdict is Verdict.WARN


def test_unsupported_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot score"):
        factor_results(TrackMeasurements(Format.FLAC), P)
