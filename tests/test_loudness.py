import math
import os
import re
import subprocess
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from qctrack.core.loudness import LoudnessAnalyzer, LoudnessResult
from qctrack.native import ebur128
from qctrack.native.ebur128 import Ebur128, Ebur128Error
from qctrack.native.mpg123 import Mpg123Decoder
from qctrack.native.vorbisfile import VorbisDecoder

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")
RATE = 48000


def sine(db: float, seconds: float = 20, freq: float = 997, channels: int = 2, rate: int = RATE):  # type: ignore[no-untyped-def]
    t = np.arange(int(seconds * rate)) / rate
    wave = (10 ** (db / 20) * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.tile(wave[:, None], (1, channels))


def measure(blocks: list[npt.NDArray[np.float32]], rate: int = RATE) -> list[LoudnessResult]:
    a = LoudnessAnalyzer(rate)
    for b in blocks:
        a.feed(b)
    return a.results()


def test_mode_flags_match_the_header() -> None:
    # Values from ebur128.h; a wrong flag would silently drop a measurement.
    assert (ebur128.MODE_M, ebur128.MODE_S, ebur128.MODE_I) == (1, 3, 5)
    assert (ebur128.MODE_LRA, ebur128.MODE_SAMPLE_PEAK, ebur128.MODE_TRUE_PEAK) == (11, 17, 49)


def test_reference_sine_is_minus_23_lufs() -> None:
    # EBU Tech 3341: a stereo 997 Hz sine at -23 dBFS reads -23.0 LUFS.
    (r,) = measure([sine(-23)])
    assert r.integrated_lufs == pytest.approx(-23.0, abs=0.1)
    assert r.sample_peak_db == pytest.approx(-23.0, abs=0.01)
    assert r.true_peak_db == pytest.approx(-23.0, abs=0.1)
    assert r.lra_lu == pytest.approx(0.0, abs=0.1)
    assert r.plr_db == pytest.approx(0.0, abs=0.2)


def test_mono_is_a_single_channel() -> None:
    # BS.1770 weights one channel by 1.0, so a mono file reads 3 LU below the same
    # signal duplicated to two channels. libebur128 and ffmpeg both follow this.
    (mono,) = measure([sine(-23, channels=1)])
    (stereo,) = measure([sine(-23, channels=2)])
    assert mono.integrated_lufs == pytest.approx(stereo.integrated_lufs - 3.01, abs=0.05)


def test_block_splitting_does_not_change_the_result() -> None:
    x = sine(-18, seconds=12)
    (whole,) = measure([x])
    (parts,) = measure([x[:12345], x[12345:300000], x[300000:]])
    assert parts.integrated_lufs == pytest.approx(whole.integrated_lufs, abs=1e-6)
    assert parts.true_peak_db == pytest.approx(whole.true_peak_db, abs=1e-6)


def test_loudness_range_of_two_levels() -> None:
    x = np.concatenate([sine(-40, seconds=20), sine(-20, seconds=20)])
    (r,) = measure([x])
    assert r.lra_lu == pytest.approx(20.0, abs=1.5)


def test_true_peak_exceeds_sample_peak() -> None:
    # fs/4 tone sampled 45 degrees off its crest: every sample is 0.707 of the true crest.
    n = np.arange(RATE * 3)
    wave = (np.sin(2 * np.pi * n / 4 + math.pi / 4)).astype(np.float32)
    x = np.stack([wave, wave], axis=1)
    (r,) = measure([x])
    assert r.sample_peak_db == pytest.approx(-3.01, abs=0.02)
    assert r.true_peak_db == pytest.approx(0.0, abs=0.15)  # oversampling filter ripple


def test_above_full_scale_is_kept() -> None:
    (r,) = measure([sine(6, seconds=3)])
    assert r.sample_peak_db == pytest.approx(6.0, abs=0.01)


def test_silence() -> None:
    (r,) = measure([np.zeros((RATE * 3, 2), dtype=np.float32)])
    assert r.integrated_lufs == -math.inf and r.sample_peak_db == -math.inf
    assert r.lra_lu == 0.0


def test_layout_and_rate_changes_give_separate_results() -> None:
    a = LoudnessAnalyzer(48000)
    a.feed(sine(-23, seconds=5))
    a.feed(sine(-23, seconds=5, channels=1))
    a.feed(sine(-23, seconds=5, channels=1, rate=22050), rate=22050)
    rs = a.results()
    assert [(r.channels, r.rate) for r in rs] == [(2, 48000), (1, 48000), (1, 22050)]
    assert [round(r.integrated_lufs, 1) for r in rs] == [-23.0, -26.0, -26.0]


def test_empty_input() -> None:
    assert measure([]) == []


def test_bad_arguments() -> None:
    with pytest.raises(Ebur128Error):
        Ebur128(0, 48000)
    with Ebur128(2, 48000) as m:
        with pytest.raises(ValueError):
            m.add(np.zeros((10, 1), dtype=np.float32))
    with pytest.raises(ValueError):
        m.loudness()


def ffmpeg_ebur128(path: Path) -> tuple[float, float, float]:
    err = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    summary = err[err.rindex("Summary:") :]
    grab = lambda key: float(re.search(rf"{key}:\s+(-?[\d.]+)", summary).group(1))  # type: ignore[union-attr]  # noqa: E731
    return grab("I"), grab("LRA"), grab("Peak")


def analyse(path: Path) -> LoudnessResult:
    dec = Mpg123Decoder(path) if path.suffix == ".mp3" else VorbisDecoder(path)
    with dec as d:
        a = LoudnessAnalyzer(0)
        for b in d.blocks():
            a.feed(b, d.rate)
    (r,) = a.results()
    return r


@real
@pytest.mark.parametrize("name", ["sample.mp3", "Track9.mp3", "sample.ogg"])
def test_matches_ffmpeg(name: str) -> None:
    r = analyse(SAMPLES / name)
    i, lra, peak = ffmpeg_ebur128(SAMPLES / name)
    print(
        f"{name}: LUFS {r.integrated_lufs:.2f} (ffmpeg {i}), LRA {r.lra_lu:.2f} (ffmpeg {lra}), "
        f"true peak {r.true_peak_db:.2f} dBTP (ffmpeg {peak}), "
        f"sample peak {r.sample_peak_db:.2f} dBFS, "
        f"PLR {r.plr_db:.2f} dB"
    )
    assert abs(r.integrated_lufs - i) <= 0.1
    # ffmpeg's own LRA code differs from libebur128's by up to 0.17 LU on loud masters
    # (the same PCM gives the same libebur128 value, so the decoder is not the cause).
    assert abs(r.lra_lu - lra) <= 0.25
    assert abs(r.true_peak_db - peak) <= 0.1
