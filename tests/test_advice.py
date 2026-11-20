"""Best-copy verdict, flags and replacement action (requirements 5.8, 5.9, 5.11)."""

from pathlib import Path

from qctrack.core.advice import Action, Advice, BestCopy, advise
from qctrack.core.detect import Format
from qctrack.core.factors import TrackMeasurements, factor_results
from qctrack.core.loudness import LoudnessResult
from qctrack.core.mp3scan import Mp3Mode, Mp3Scan
from qctrack.core.pcm import ChannelResult, ClippingResult
from qctrack.core.profile import Profile, load_default, load_profile
from qctrack.core.scoring import score_track
from qctrack.core.tags import TagInfo
from test_factors import declared, lame, mp3, pcm, spectrum

P = load_default()
TAGS = TagInfo(formats=["ID3v2.4"], title="t")


def advice(scan: Mp3Scan, profile: Profile = P, tags: TagInfo = TAGS, **kw: object) -> Advice:
    m = TrackMeasurements(Format.MP3, mp3=scan, **pcm(**kw))  # type: ignore[arg-type]
    factors = factor_results(m, profile)
    return advise(m, tags, factors, score_track(factors, profile), profile)


def test_clean_320_is_kept() -> None:
    a = advice(mp3())
    assert (a.best_copy, a.action, a.reason) == (BestCopy.BEST, Action.KEEP, "")


def test_transcode_is_replaced_from_another_source() -> None:
    a = advice(mp3(), spectrum=[spectrum(cutoff=16000.0)])
    assert a.best_copy is BestCopy.LOSSY_SOURCE and a.action is Action.REPLACE_SOURCE
    assert a.reason.startswith("claims high, content supports very_low (cutoff 16.00 kHz")


def test_damage_comes_first() -> None:
    a = advice(mp3(truncated_bytes=500, **declared(2000)), spectrum=[spectrum(cutoff=16000.0)])
    assert a.action is Action.REPLACE_DAMAGED and a.action.priority == 1
    assert a.reason == "F2.02: truncated, 26.1 s of 52.2 s missing"
    assert a.best_copy is BestCopy.LOSSY_SOURCE  # the verdict is still reported


def test_low_bitrate_encode_is_upgraded() -> None:
    a = advice(mp3(average_bitrate_kbps=128.0, lame=lame(128)), spectrum=[spectrum(17000.0)])
    assert a.best_copy is BestCopy.LOW_BITRATE and a.action is Action.REPLACE_LOW_BITRATE
    assert "128 kbps avg" in a.reason


def test_high_vbr_without_a_preset_and_mono_are_not_upgrades() -> None:
    # FFmpeg's libmp3lame stores no preset: a V0-like average counts as high quality.
    vbr = mp3(average_bitrate_kbps=242.0, lame=lame(0, encoder="Lavc63.1."), mode=Mp3Mode.VBR)
    assert advice(vbr).action is Action.KEEP
    # Mono needs half the bitrate of stereo: 160 kbps mono is as good as 320 stereo.
    mono = mp3(average_bitrate_kbps=160.0, lame=lame(160), channel_mode="Mono")
    a = advice(mono, channels=[ChannelResult(1, 1000, [-20.0])])
    assert a.action is Action.KEEP and "mono_content" in a.flags


def test_poor_mastering_suggests_another_master() -> None:
    clipped = ClippingResult(samples=1_000_000, clipped_samples=20_000)  # 2%
    loud = LoudnessResult(2, 44100, 1000, 0.0, 2.5, -5.0, 1.5)
    a = advice(mp3(), clipping=clipped, loudness=[loud])
    assert a.best_copy is BestCopy.BEST and a.action is Action.CONSIDER_MASTER
    assert a.reason.startswith("mastering ")


def test_mastering_threshold_comes_from_the_profile(tmp_path: Path) -> None:
    off = tmp_path / "p.toml"
    off.write_text("[advice]\nconsider_master_below = 0\n")
    clipped = ClippingResult(samples=1_000_000, clipped_samples=20_000)
    loud = LoudnessResult(2, 44100, 1000, 0.0, 2.5, -5.0, 1.5)
    assert advice(mp3(), load_profile(off), clipping=clipped, loudness=[loud]).action is Action.KEEP


def test_quick_scan_is_undetermined() -> None:
    m = TrackMeasurements(Format.MP3, mp3=mp3())
    factors = factor_results(m, P)
    a = advise(m, TAGS, factors, score_track(factors, P), P)
    assert a.best_copy is BestCopy.UNDETERMINED and a.action is Action.UNDETERMINED
    assert a.reason == "quick scan: run a full scan"


def test_flags() -> None:
    assert "unknown_encoder" in advice(mp3(encoder=None, lame=None)).flags
    assert "non_lame_encoder" in advice(mp3(encoder="iTunes", lame=None)).flags
    assert "non_lame_encoder" not in advice(mp3(encoder="Lavf58.29", lame=None)).flags
    low = mp3(sample_rate=32000, sample_rates_seen={32000})
    assert "low_sample_rate" in advice(low, spectrum=[spectrum(15400.0, rate=32000)]).flags
    assert "no_tags" in advice(mp3(), tags=TagInfo()).flags
    big = mp3(metadata_bytes={"ID3v2": 2_000_000})
    assert "cover_art_large" in advice(big).flags
    assert advice(mp3()).flags == ()  # nothing to report on a clean, long, tagged file


def test_action_priorities() -> None:
    assert [a.priority for a in Action] == list(range(1, 8))
    assert Action.REPLACE_DAMAGED.priority < Action.REPLACE_SOURCE.priority < Action.KEEP.priority
