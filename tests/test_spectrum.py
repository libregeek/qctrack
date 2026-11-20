import os
import subprocess
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from qctrack.core.authenticity import (
    FADE_PROTECT_DB,
    Band,
    Verdict,
    claimed_band,
    claimed_vs_effective,
    declared_vs_measured,
    effective_band,
)
from qctrack.core.mp3scan import scan_mp3
from qctrack.core.spectrum import (
    FFT_SIZE,
    SpectrumAnalyzer,
    SpectrumResult,
    analyse_spectrum,
)
from qctrack.native.mpg123 import Mpg123Decoder
from qctrack.native.vorbisfile import VorbisDecoder

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")
RATE = 44100
Pcm = npt.NDArray[np.float32]


def lowpassed_noise(
    cutoff: float | None, seconds: float = 12, rate: int = RATE, level: float = 0.1, seed: int = 1
) -> Pcm:
    """Stereo white noise with an FFT brick-wall lowpass at ``cutoff`` Hz (None: no filter)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    out = np.empty((n, 2), dtype=np.float32)
    for ch in range(2):
        x = rng.standard_normal(n)
        if cutoff is not None:
            spec = np.fft.rfft(x)
            spec[np.fft.rfftfreq(n, 1 / rate) > cutoff] = 0
            x = np.fft.irfft(spec, n)
        out[:, ch] = (x / x.std() * level).astype(np.float32)
    return out


def measure(pcm: Pcm, rate: int = RATE, block: int = 8192) -> SpectrumResult:
    a = SpectrumAnalyzer(rate)
    for i in range(0, len(pcm), block):
        a.feed(pcm[i : i + block])
    (r,) = a.results()
    return r


def test_flat_noise_has_no_cutoff() -> None:
    r = measure(lowpassed_noise(None))
    assert r.measured and not r.has_cliff
    assert r.cutoff_hz == r.nyquist_hz == RATE / 2
    assert r.upsampled_from_hz is None


@pytest.mark.parametrize("cutoff", [16000, 17500, 19000, 20500])
def test_brick_wall_is_found(cutoff: int) -> None:
    r = measure(lowpassed_noise(cutoff))
    assert r.has_cliff
    assert r.cutoff_hz == pytest.approx(cutoff, abs=250)
    assert r.drop_db > 60
    assert r.sharpness_db_per_khz > 30  # F3.04: lossy-style edge


def test_gentle_rolloff_is_not_a_cliff() -> None:
    # Spectrum falling 6 dB/kHz from 10 kHz up: natural, below the 20 dB/kHz threshold.
    freqs = np.arange(FFT_SIZE // 2 + 1) * RATE / FFT_SIZE
    db = -40.0 - np.maximum(0, freqs - 10000) * 6 / 1000
    r = analyse_spectrum(db, RATE)
    assert not r.has_cliff and r.cutoff_hz == RATE / 2
    assert r.sharpness_db_per_khz == pytest.approx(6.0, abs=0.5)


def test_tilted_noise_floor_does_not_hide_the_cliff() -> None:
    # Regression: real decoders leave a floor sloping 15 dB over the empty band.
    freqs = np.arange(FFT_SIZE // 2 + 1) * RATE / FFT_SIZE
    db = np.where(freqs < 17000, -70.0, -140.0 - (freqs - 17000) * 3 / 1000)
    r = analyse_spectrum(db, RATE)
    assert r.has_cliff and r.cutoff_hz == pytest.approx(17000, abs=300)


def test_block_splitting_does_not_change_the_result() -> None:
    pcm = lowpassed_noise(17000)
    a, b = measure(pcm, block=8192), measure(pcm, block=3001)
    assert a.cutoff_hz == b.cutoff_hz and a.frames == b.frames
    assert a.mean_spectrum_db is not None and b.mean_spectrum_db is not None
    assert np.allclose(a.mean_spectrum_db, b.mean_spectrum_db)


def test_quiet_passages_do_not_count() -> None:
    # Full-band noise at -50 dBFS RMS around a band-limited loud passage: the loud part wins.
    loud = lowpassed_noise(16000, seconds=10, level=0.2)
    quiet = lowpassed_noise(None, seconds=3, level=0.003, seed=2)
    r = measure(np.concatenate([quiet, loud, quiet]))
    assert r.has_cliff and r.cutoff_hz == pytest.approx(16000, abs=250)


def test_silence_and_short_input_are_not_measured() -> None:
    assert not measure(np.zeros((RATE * 3, 2), dtype=np.float32)).measured
    assert not measure(lowpassed_noise(16000, seconds=0.1)).measured


def test_mono_works() -> None:
    r = measure(lowpassed_noise(18000)[:, :1])
    assert r.has_cliff and r.cutoff_hz == pytest.approx(18000, abs=250)


def test_upsampled_content_is_reported() -> None:
    # 22.05 kHz source resampled to 44.1 kHz: content stops just under 11.025 kHz.
    r = measure(lowpassed_noise(10300))
    assert r.has_cliff and r.upsampled_from_hz == 22050
    # An ordinary 16 kHz lowpass (a 128 kbps encode) is not reported as upsampling.
    assert measure(lowpassed_noise(16500)).upsampled_from_hz is None


def test_rate_change_gives_separate_results() -> None:
    a = SpectrumAnalyzer(44100)
    a.feed(lowpassed_noise(17000, rate=44100))
    a.feed(lowpassed_noise(10000, rate=22050), rate=22050)
    first, second = a.results()
    assert (first.rate, second.rate) == (44100, 22050)
    assert first.cutoff_hz == pytest.approx(17000, abs=250)
    assert second.cutoff_hz == pytest.approx(10000, abs=250)


def test_empty_input() -> None:
    assert SpectrumAnalyzer(RATE).results() == []


# --- derived factors --------------------------------------------------------------------


def fake(cutoff: float, rate: int = RATE, fade: float | None = 3.0) -> SpectrumResult:
    cliff = cutoff < rate / 2 - 500
    return SpectrumResult(
        rate=rate, measured=True, nyquist_hz=rate / 2, cutoff_hz=cutoff, has_cliff=cliff,
        fade_db=fade if cliff else None,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("cutoff", "band"),
    [(22050, Band.HIGH), (19500, Band.HIGH), (19499, Band.MEDIUM), (17250, Band.MEDIUM),
     (17249, Band.LOW), (16500, Band.LOW), (16499, Band.VERY_LOW), (11000, Band.VERY_LOW)],
)  # fmt: skip
def test_effective_band_edges(cutoff: float, band: Band) -> None:
    assert effective_band(cutoff) is band


def test_claimed_band_from_bitrate_and_preset() -> None:
    assert claimed_band(320) is Band.HIGH and claimed_band(256) is Band.HIGH
    assert claimed_band(255) is Band.MEDIUM and claimed_band(160) is Band.MEDIUM
    assert claimed_band(159) is Band.LOW and claimed_band(128) is Band.LOW
    assert claimed_band(127) is Band.VERY_LOW
    # A preset overrides the average: V2 at 190 kbps is Medium, V0 at 240 kbps is High.
    assert claimed_band(190, "V2") is Band.MEDIUM and claimed_band(240, "V0") is Band.HIGH
    assert claimed_band(120, "V5") is Band.LOW and claimed_band(320, "320 kbps") is Band.HIGH


def test_claimed_vs_effective_verdicts() -> None:
    ok = claimed_vs_effective(fake(20500), 320)
    one = claimed_vs_effective(fake(18000), 320)
    two = claimed_vs_effective(fake(16200), 320)
    assert ok and ok.verdict is Verdict.PASS and not ok.triggers_g4
    assert one and one.verdict is Verdict.WARN and one.bands_lower == 1
    assert two and two.verdict is Verdict.FAIL and two.bands_lower == 3 and two.triggers_g4
    # Content better than claimed is never penalised.
    better = claimed_vs_effective(fake(20500), 128)
    assert better and better.bands_lower == 0 and better.verdict is Verdict.PASS


def test_claimed_vs_effective_not_evaluated() -> None:
    assert claimed_vs_effective(fake(10000, rate=22050), 128) is None  # low sample rate
    # 32 kHz: Nyquist (16 kHz) bounds the cutoff, so the CD-rate table does not apply (C5).
    assert claimed_vs_effective(fake(15400, rate=32000), 160) is None
    assert claimed_vs_effective(SpectrumResult(rate=RATE), 320) is None  # unmeasured


def test_no_cliff_means_full_band() -> None:
    # Without a cliff, the cutoff value is not evidence of a limited band.
    r = SpectrumResult(rate=48000, measured=True, nyquist_hz=24000, cutoff_hz=15000)
    v = claimed_vs_effective(r, 320)
    assert v and v.effective is Band.HIGH and v.verdict is Verdict.PASS


def test_fading_content_before_the_wall_is_not_a_transcode() -> None:
    """N1: LAME drops faint highs (sfb21) even at 320 kbps, leaving a wall near 16 kHz that
    is reached only after the content has faded. That is at most a Warn, never gate G4."""
    faded = claimed_vs_effective(fake(16200, fade=FADE_PROTECT_DB), 320)
    assert faded and faded.verdict is Verdict.WARN and faded.protected
    assert faded.bands_lower == 3 and not faded.triggers_g4
    full = claimed_vs_effective(fake(16200, fade=FADE_PROTECT_DB - 0.1), 320)
    assert full and full.verdict is Verdict.FAIL and not full.protected
    # A one-band Warn is not changed, and a Pass stays a Pass.
    one = claimed_vs_effective(fake(18000, fade=30.0), 320)
    assert one and one.verdict is Verdict.WARN and not one.protected


def test_declared_vs_measured() -> None:
    d = declared_vs_measured(fake(20300), 20500)
    assert d and d.verdict is Verdict.PASS and d.delta_hz == pytest.approx(200)
    d = declared_vs_measured(fake(19000), 20500)
    assert d and d.verdict is Verdict.WARN
    d = declared_vs_measured(fake(16200), 20500)
    assert d and d.verdict is Verdict.FAIL
    d = declared_vs_measured(fake(22050), 20500)  # content beyond the declaration
    assert d and d.verdict is Verdict.PASS and d.delta_hz < 0
    assert declared_vs_measured(fake(16200), 0) is None
    assert declared_vs_measured(SpectrumResult(rate=RATE), 20500) is None


# --- through real encoders --------------------------------------------------------------


def encode(out: Path, codec: str, pcm: Pcm, args: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", "2", "-i", "-"]
        + ["-c:a", codec, *args, str(out)],
        input=pcm.tobytes(),
        check=True,
    )


def analyse(path: Path) -> SpectrumResult:
    dec = Mpg123Decoder(path) if path.suffix == ".mp3" else VorbisDecoder(path)
    a = SpectrumAnalyzer(0)
    with dec as d:
        for b in d.blocks():
            a.feed(b, d.rate)
    (r,) = a.results()
    return r


def decode_all(path: Path) -> Pcm:
    with Mpg123Decoder(path) as d:
        return np.concatenate(list(d.blocks()))


def test_128_reencoded_to_320_is_flagged(tmp_path: Path) -> None:
    source = lowpassed_noise(None, seconds=15)
    original128 = tmp_path / "original128.mp3"
    encode(original128, "libmp3lame", source, ["-b:a", "128k"])
    transcode = tmp_path / "transcode320.mp3"
    encode(transcode, "libmp3lame", decode_all(original128), ["-b:a", "320k"])
    genuine = tmp_path / "genuine320.mp3"
    encode(genuine, "libmp3lame", source, ["-b:a", "320k"])

    out = {}
    for path in (original128, transcode, genuine):
        scan = scan_mp3(path)
        spec = analyse(path)
        verdict = claimed_vs_effective(spec, scan.average_bitrate_kbps)
        assert verdict is not None
        out[path.stem] = (spec, scan, verdict)
        print(
            f"{path.stem}: {scan.average_bitrate_kbps:.0f} kbps, cutoff {spec.cutoff_hz:.0f} Hz, "
            f"sharpness {spec.sharpness_db_per_khz:.0f} dB/kHz -> claimed {verdict.claimed.name}, "
            f"effective {verdict.effective.name}, {verdict.verdict.value}"
        )

    assert out["original128"][2].verdict is Verdict.PASS  # claims 128, delivers 128
    assert out["genuine320"][2].verdict is Verdict.PASS
    fake_verdict = out["transcode320"][2]
    assert fake_verdict.verdict is Verdict.FAIL and fake_verdict.triggers_g4
    # Content runs at full level up to the transcode's wall, unlike a source that fades.
    assert fake_verdict.fade_db is not None and fake_verdict.fade_db < FADE_PROTECT_DB
    assert out["transcode320"][0].cutoff_hz < 17500 < out["genuine320"][0].cutoff_hz
    # F3.03: LAME declares ~20.5 kHz in the transcode, but the content stops far lower.
    lowpass = out["transcode320"][1].lame.lowpass_hz if out["transcode320"][1].lame else 0
    declared = declared_vs_measured(out["transcode320"][0], lowpass)
    if declared is not None:
        assert declared.verdict is Verdict.FAIL
    genuine_declared = declared_vs_measured(
        out["genuine320"][0],
        out["genuine320"][1].lame.lowpass_hz if out["genuine320"][1].lame else 0,
    )
    if genuine_declared is not None:
        assert genuine_declared.verdict is Verdict.PASS


def encode_dark_source(out: Path, corner: int) -> None:
    """A genuine 320 kbps encode of a dark source: pink noise with slow level changes,
    falling a further 24 dB per octave above ``corner`` Hz (two 2-pole lowpass filters), so
    its high frequencies fade gradually with no wall of their own."""
    noise = "anoisesrc=d=15:c=pink:r=44100:a=0.3:seed={}"
    graph = (
        "[0][1]amerge=inputs=2,volume='0.35+0.3*sin(2*PI*t/7)':eval=frame,"
        f"lowpass=f={corner}:poles=2,lowpass=f={corner}:poles=2"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", noise.format(1), "-f", "lavfi",
         "-i", noise.format(2), "-filter_complex", graph, "-c:a", "libmp3lame", "-b:a", "320k",
         str(out)],
        check=True,
    )  # fmt: skip


@pytest.mark.parametrize("corner", [5000, 9000])
def test_dark_source_at_320_is_not_a_transcode(tmp_path: Path, corner: int) -> None:
    """N1 through the real encoder: LAME drops the faint highs of a dark source even at
    320 kbps, leaving a wall near 16 kHz, but the fade before it keeps gate G4 off."""
    path = tmp_path / f"dark{corner}.mp3"
    encode_dark_source(path, corner)
    spec = analyse(path)
    v = claimed_vs_effective(spec, scan_mp3(path).average_bitrate_kbps)
    print(f"dark source from {corner} Hz at 320 kbps: cutoff {spec.cutoff_hz:.0f} Hz, fade "
          f"{spec.fade_db} dB -> {v and v.verdict.value}")  # fmt: skip
    assert v is not None
    # The encoder's wall lands two or more bands below the 320 kbps claim ...
    assert spec.has_cliff and spec.cutoff_hz < 16500 and v.bands_lower >= 2
    # ... but the content fades before it, so this is a Warn and never gate G4.
    assert v.protected and v.verdict is Verdict.WARN and not v.triggers_g4


def test_vorbis_cutoff_is_measured(tmp_path: Path) -> None:
    path = tmp_path / "noise.ogg"
    encode(path, "libvorbis", lowpassed_noise(None, seconds=10), ["-q:a", "2"])
    r = analyse(path)
    print(f"ogg q2: cutoff {r.cutoff_hz:.0f} Hz, cliff {r.has_cliff}")
    assert r.measured and r.cutoff_hz < 21000 and r.has_cliff


@real
@pytest.mark.parametrize(
    "name",
    ["sample.mp3", "Track9.mp3", "sample.ogg", "Dooreyaanu Keralam.mp3", "Sundara Ravanan.mp3"],
)
def test_real_samples(name: str) -> None:
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"{name} not available")
    r = analyse(path)
    line = (
        f"{name}: cutoff {r.cutoff_hz:.0f} Hz, cliff {r.has_cliff}, plateau {r.plateau_db:.0f} dB, "
        f"floor {r.floor_db:.0f} dB, sharpness {r.sharpness_db_per_khz:.0f} dB/kHz, "
        f"upsampled from {r.upsampled_from_hz}"
    )
    if path.suffix == ".mp3":
        scan = scan_mp3(path)
        v = claimed_vs_effective(
            r, scan.average_bitrate_kbps, scan.lame.preset_name if scan.lame else None
        )
        line += f" | {scan.average_bitrate_kbps:.0f} kbps -> {v.verdict.value if v else 'n/a'}"
    print(line)
    assert r.measured and r.frames > 0
