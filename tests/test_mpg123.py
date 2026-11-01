import os
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from helpers import ffmpeg_reference, match_db
from qctrack.native.mpg123 import (
    BLOCK_BYTES,
    FLAG_FORCE_FLOAT,
    FLAG_QUIET,
    PARAM_FLAGS,
    PARAM_RVA,
    Mpg123Decoder,
    Mpg123Error,
)

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")


def decode_all(path: Path) -> tuple[npt.NDArray[np.float32], int, int]:
    with Mpg123Decoder(path) as d:
        blocks = list(d.blocks())
        return np.concatenate(blocks), d.rate, d.channels


@real
@pytest.mark.parametrize("name", ["sample.mp3", "Track9.mp3"])
def test_decode_matches_ffmpeg(name: str) -> None:
    pcm, rate, channels = decode_all(SAMPLES / name)
    ref = ffmpeg_reference(SAMPLES / name, channels, rate)
    assert pcm.dtype == np.float32 and pcm.shape[1] == channels
    assert abs(len(pcm) - len(ref)) <= 1
    db = match_db(pcm, ref)
    print(f"{name}: {len(pcm)} frames, match {db:.1f} dB")
    assert db > 100


@real
def test_blocks_are_bounded() -> None:
    with Mpg123Decoder(SAMPLES / "sample.mp3") as d:
        sizes = [b.nbytes for b in d.blocks()]
    assert max(sizes) <= BLOCK_BYTES
    assert all(s == BLOCK_BYTES for s in sizes[:-1])


@real
def test_peak_above_full_scale_is_kept() -> None:
    pcm, _, _ = decode_all(SAMPLES / "sample.mp3")
    assert float(np.abs(pcm).max()) > 1.0  # float output is not clipped


@real
def test_settings_quiet_float_rva_off() -> None:
    with Mpg123Decoder(SAMPLES / "sample.mp3") as d:
        assert d.param(PARAM_RVA) == 0
        flags = d.param(PARAM_FLAGS)
        assert flags & FLAG_QUIET and flags & FLAG_FORCE_FLOAT


def test_missing_file_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        Mpg123Decoder(tmp_path / "nope.mp3")


def test_non_mp3_raises_decoder_error(tmp_path: Path) -> None:
    p = tmp_path / "junk.mp3"
    p.write_bytes(os.urandom(5000))
    with pytest.raises(Mpg123Error), Mpg123Decoder(p) as d:
        list(d.blocks())


@real
def test_truncated_file_still_decodes(tmp_path: Path) -> None:
    p = tmp_path / "cut.mp3"
    p.write_bytes((SAMPLES / "sample.mp3").read_bytes()[:200_000])
    pcm, _, _ = decode_all(p)
    assert len(pcm) > 1000


def test_close_is_idempotent_and_blocks_after_close_fail() -> None:
    if not SAMPLES.is_dir():
        pytest.skip("samples folder not available")
    d = Mpg123Decoder(SAMPLES / "sample.mp3")
    d.close()
    d.close()
    with pytest.raises(ValueError):
        list(d.blocks())
