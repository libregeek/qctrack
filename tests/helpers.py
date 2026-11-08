import subprocess
from pathlib import Path

import numpy as np
import numpy.typing as npt


def ffmpeg_reference(path: object, channels: int, rate: int) -> npt.NDArray[np.float32]:
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            str(channels),
            "-ar",
            str(rate),
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32).reshape(-1, channels)


def match_db(a: npt.NDArray[np.float32], b: npt.NDArray[np.float32]) -> float:
    n = min(len(a), len(b))
    a64, b64 = a[:n].astype(np.float64), b[:n].astype(np.float64)
    err = float(np.sqrt(np.mean((a64 - b64) ** 2)))
    ref = float(np.sqrt(np.mean(b64**2)))
    return 200.0 if err == 0 else 20 * float(np.log10(ref / err))


def tone(seconds: int = 12, rate: int = 44100, broadband: bool = True) -> bytes:
    """A music-like stereo signal. Band-limited to ~12 kHz unless ``broadband``, which adds
    quiet noise so that content reaches the top of the spectrum, as real music does."""
    rng = np.random.default_rng(7)
    x = np.arange(rate * seconds) / rate
    sig = sum(
        rng.uniform(0.01, 0.05)
        * np.sin(2 * np.pi * rng.uniform(100, 12000) * x + rng.uniform(0, 6))
        for _ in range(30)
    )
    sig = (sig * (1 + 0.5 * np.sin(2 * np.pi * 2 * x))).astype(np.float32)
    pcm = np.stack([sig, sig * 0.8], axis=1)
    if broadband:
        pcm += rng.standard_normal(pcm.shape, dtype=np.float32) * np.float32(0.01)
    return pcm.astype(np.float32).tobytes()


def encode(out: Path, *codec_args: str, broadband: bool = True) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", "2", "-i", "-"]
        + list(codec_args)
        + [str(out)],
        input=tone(broadband=broadband),
        check=True,
    )
    return out
