import os
import subprocess
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from helpers import ffmpeg_reference, match_db
from qctrack.native.vorbisfile import BLOCK_BYTES, VorbisDecoder, VorbisError

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")


def decode_all(path: Path) -> tuple[npt.NDArray[np.float32], int, int]:
    with VorbisDecoder(path) as d:
        blocks = list(d.blocks())
        return np.concatenate(blocks), d.rate, d.channels


@real
def test_decode_matches_ffmpeg() -> None:
    pcm, rate, channels = decode_all(SAMPLES / "sample.ogg")
    ref = ffmpeg_reference(SAMPLES / "sample.ogg", channels, rate)
    assert pcm.dtype == np.float32 and pcm.shape[1] == channels
    assert len(pcm) == len(ref)
    db = match_db(pcm, ref)
    print(f"sample.ogg: {len(pcm)} frames, match {db:.1f} dB")
    assert db > 100


@real
def test_blocks_are_bounded_and_full() -> None:
    with VorbisDecoder(SAMPLES / "sample.ogg") as d:
        sizes = [b.nbytes for b in d.blocks()]
    assert max(sizes) <= BLOCK_BYTES
    assert all(s == BLOCK_BYTES for s in sizes[:-1])


@real
def test_peak_above_full_scale_is_kept() -> None:
    pcm, _, _ = decode_all(SAMPLES / "sample.ogg")
    assert float(np.abs(pcm).max()) > 1.0


@real
def test_mp3_is_rejected() -> None:
    with pytest.raises(VorbisError) as exc:
        VorbisDecoder(SAMPLES / "sample.mp3")
    assert exc.value.code == -132


def test_missing_file_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        VorbisDecoder(tmp_path / "nope.ogg")


def test_junk_raises_decoder_error(tmp_path: Path) -> None:
    p = tmp_path / "junk.ogg"
    p.write_bytes(os.urandom(5000))
    with pytest.raises(VorbisError):
        VorbisDecoder(p)


@real
def test_truncated_file_still_decodes(tmp_path: Path) -> None:
    p = tmp_path / "cut.ogg"
    p.write_bytes((SAMPLES / "sample.ogg").read_bytes()[:200_000])
    pcm, _, _ = decode_all(p)
    assert len(pcm) > 1000


@real
def test_close_is_idempotent_and_blocks_after_close_fail() -> None:
    d = VorbisDecoder(SAMPLES / "sample.ogg")
    d.close()
    d.close()
    with pytest.raises(ValueError):
        list(d.blocks())


# --- chained streams (generated with ffmpeg; nothing is committed) --------------------


def _encode(src: Path, dst: Path, *args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src), *args, "-c:a", "libvorbis", str(dst)],
        check=True,
    )


@real
def test_chained_stream_with_format_changes(tmp_path: Path) -> None:
    if subprocess.run(["ffmpeg", "-encoders"], capture_output=True).stdout.find(b"libvorbis") < 0:
        pytest.skip("ffmpeg has no libvorbis encoder")
    src = SAMPLES / "sample.ogg"
    parts = [tmp_path / "a.ogg", tmp_path / "b.ogg", tmp_path / "c.ogg"]
    _encode(src, parts[0], "-t", "3")
    _encode(src, parts[1], "-t", "2", "-ac", "1", "-ar", "22050")  # mono, other rate
    _encode(src, parts[2], "-ss", "5", "-t", "1")
    chained = tmp_path / "chain.ogg"
    chained.write_bytes(b"".join(p.read_bytes() for p in parts))

    expected = [decode_all(p) for p in parts]
    seen: list[tuple[int, int, int]] = []  # (rate, channels, frames) per block
    pieces: list[npt.NDArray[np.float32]] = []
    with VorbisDecoder(chained) as d:
        for block in d.blocks():
            assert block.shape[1] == d.channels  # attributes describe the block just yielded
            seen.append((d.rate, d.channels, len(block)))
            pieces.append(block)
        assert d.format_changes == 2

    # Per-block formats follow the three parts in order, with no mixing.
    formats = [(r, c) for r, c, _ in seen]
    assert [f for i, f in enumerate(formats) if i == 0 or f != formats[i - 1]] == [
        (44100, 2),
        (22050, 1),
        (44100, 2),
    ]
    # Each part's samples are identical to decoding that part alone.
    for rate, channels in {(44100, 2), (22050, 1)}:
        got = [p for p, (r, c, _) in zip(pieces, seen, strict=True) if (r, c) == (rate, channels)]
        want = [pcm for pcm, r, c in expected if (r, c) == (rate, channels)]
        assert np.array_equal(np.concatenate(got), np.concatenate(want))
    assert sum(len(p) for p in pieces) == sum(len(pcm) for pcm, _, _ in expected)
