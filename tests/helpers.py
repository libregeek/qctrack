import subprocess

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
