import os
import subprocess
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from qctrack.core.pcm import ChannelAnalyzer, ClippingAnalyzer
from qctrack.native.mpg123 import Mpg123Decoder
from qctrack.native.vorbisfile import VorbisDecoder

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")


def clip(blocks: list[npt.NDArray[np.float32]]):  # type: ignore[no-untyped-def]
    a = ClippingAnalyzer()
    for b in blocks:
        a.feed(b)
    return a.result()


def col(values: list[float], channels: int = 1) -> npt.NDArray[np.float32]:
    return np.tile(np.array(values, dtype=np.float32)[:, None], (1, channels))


def test_run_of_three_counts_run_of_two_does_not() -> None:
    r = clip([col([0, 1, 1, 1, 0, -1, -1, 0, 0.5])])
    assert (r.runs, r.clipped_samples, r.longest_run) == (1, 3, 3)
    assert r.samples == 9


def test_negative_and_above_full_scale_count() -> None:
    r = clip([col([0, -1.2, -1.0, -0.9995, 0])])
    assert (r.runs, r.clipped_samples) == (1, 3)
    assert r.max_abs == pytest.approx(1.2)


def test_run_continues_across_blocks() -> None:
    r = clip([col([0, 1, 1]), col([1, 1, 0]), col([0.2])])
    assert (r.runs, r.clipped_samples, r.longest_run) == (1, 4, 4)


def test_two_plus_one_across_a_boundary_is_one_run() -> None:
    assert clip([col([0, 1, 1]), col([1, 0])]).runs == 1


def test_run_of_two_split_across_blocks_is_not_clipping() -> None:
    assert clip([col([0, 1]), col([1, 0])]).runs == 0


def test_run_spanning_whole_blocks() -> None:
    r = clip([col([1] * 5), col([1] * 5), col([1] * 5), col([0])])
    assert (r.runs, r.clipped_samples) == (1, 15)


def test_run_open_at_end_is_counted() -> None:
    assert clip([col([0, 1, 1, 1])]).clipped_samples == 3


def test_channels_do_not_join_into_one_run() -> None:
    block = np.zeros((6, 2), dtype=np.float32)
    block[:2, 0] = 1
    block[2:4, 1] = 1  # two short runs in different channels, not one run of four
    assert clip([block]).runs == 0


def test_layout_change_ends_runs() -> None:
    # The mono run of 3 counts; the stereo run of 2 starts fresh and does not.
    r = clip([col([0, 1, 1, 1], 1), col([1, 1, 0], 2)])
    assert (r.runs, r.clipped_samples) == (1, 3)
    # A run of 2 before the change plus 1 after is not joined into a run of 3.
    assert clip([col([0, 1, 1], 1), col([1, 0], 2)]).runs == 0


def test_percent() -> None:
    r = clip([col([1, 1, 1] + [0] * 997)])
    assert r.clipped_percent == pytest.approx(0.3)


def test_empty() -> None:
    assert clip([]).clipped_percent == 0.0


def tone(seconds: float, left: float, right: float, rate: int = 44100):  # type: ignore[no-untyped-def]
    t = np.arange(int(seconds * rate)) / rate
    wave = np.sin(2 * np.pi * 440 * t)
    return np.stack([wave * left, wave * right], axis=1).astype(np.float32)


def chan(blocks: list[npt.NDArray[np.float32]]):  # type: ignore[no-untyped-def]
    a = ChannelAnalyzer()
    for b in blocks:
        a.feed(b)
    return a.results()


def test_healthy_stereo() -> None:
    rng = np.random.default_rng(1)
    x = (rng.standard_normal((20000, 2)) * 0.1).astype(np.float32)
    (r,) = chan([x])
    assert r.correlation is not None and abs(r.correlation) < 0.05
    assert r.dead_channels == [] and not r.fake_stereo and not r.inverted
    assert r.rms_db[0] == pytest.approx(-20, abs=0.3)


def test_dead_channel() -> None:
    (r,) = chan([tone(1, 0.5, 0.0)])
    assert r.dead_channels == [1]
    assert r.correlation is None  # constant channel has no correlation


def test_fake_stereo() -> None:
    (r,) = chan([tone(1, 0.5, 0.5)])
    assert r.fake_stereo and r.correlation == pytest.approx(1.0)
    assert r.side_to_mid_db is not None and r.side_to_mid_db < -100


def test_inverted_polarity() -> None:
    (r,) = chan([tone(1, 0.5, -0.5)])
    assert r.inverted and r.correlation == pytest.approx(-1.0)


def test_block_splitting_does_not_change_result() -> None:
    rng = np.random.default_rng(2)
    x = (rng.standard_normal((10000, 2)) * 0.1).astype(np.float32)
    x[:, 1] += 0.5 * x[:, 0]
    (whole,) = chan([x])
    (parts,) = chan([x[:3333], x[3333:7000], x[7000:]])
    assert parts.correlation == pytest.approx(whole.correlation)
    assert parts.rms_db == pytest.approx(whole.rms_db)


def test_digital_silence_is_not_a_dead_channel() -> None:
    (r,) = chan([np.zeros((1000, 2), dtype=np.float32)])
    assert r.dead_channels == [] and r.correlation is None


def test_mono_has_only_rms() -> None:
    (r,) = chan([col([0.5, -0.5] * 100)])
    assert (
        r.channels == 1 and r.correlation is None and r.rms_db[0] == pytest.approx(-6.02, abs=0.01)
    )


def test_layout_change_gives_two_results() -> None:
    rs = chan([tone(0.1, 0.5, 0.5), col([0.1] * 100)])
    assert [r.channels for r in rs] == [2, 1]


def encode(out: Path, codec: str, pcm: npt.NDArray[np.float32]) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "f32le",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-i",
            "-",
            "-c:a",
            codec,
            str(out),
        ],
        input=pcm.tobytes(),
        check=True,
    )


def analyse(path: Path):  # type: ignore[no-untyped-def]
    clipping, channels = ClippingAnalyzer(), ChannelAnalyzer()
    dec = Mpg123Decoder(path) if path.suffix == ".mp3" else VorbisDecoder(path)
    with dec as d:
        for b in d.blocks():
            clipping.feed(b)
            channels.feed(b)
    return clipping.result(), channels.results()


@pytest.mark.parametrize(("suffix", "codec"), [(".mp3", "libmp3lame"), (".ogg", "libvorbis")])
def test_generated_files(tmp_path: Path, suffix: str, codec: str) -> None:
    wave = tone(3, 1.0, 1.0)[:, :1] * np.array([[1.0, 0.7]], dtype=np.float32)  # L, R differ
    wave[:, 1] *= -1  # R inverted too, to exercise the channel flags
    clipped = tmp_path / f"clipped{suffix}"
    encode(clipped, codec, np.clip(wave * 4, -1, 1))  # flat tops on every cycle
    clean = tmp_path / f"clean{suffix}"
    encode(clean, codec, wave * 0.3)
    bad, _ = analyse(clipped)
    good, chans = analyse(clean)
    print(
        f"{suffix}: clipped {bad.clipped_percent:.3f}% in {bad.runs} runs; "
        f"clean {good.clipped_percent:.4f}%"
    )
    assert bad.runs > 100 and bad.clipped_percent > 0.1
    assert good.runs == 0 and good.clipped_percent == 0.0
    assert chans[0].inverted  # R is the inverted L


@real
@pytest.mark.parametrize("name", ["sample.mp3", "Track9.mp3", "sample.ogg"])
def test_real_samples(name: str) -> None:
    clip_res, chans = analyse(SAMPLES / name)
    c = chans[0]
    print(
        f"{name}: clipped {clip_res.clipped_percent:.4f}% "
        f"({clip_res.runs} runs, longest {clip_res.longest_run}), "
        f"max {clip_res.max_abs:.4f}, rms {[round(v, 1) for v in c.rms_db]} dB, "
        f"corr {c.correlation}, side/mid {c.side_to_mid_db}"
    )
    assert clip_res.samples > 0
