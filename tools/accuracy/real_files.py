"""Compare our decoders and peak measurements with FFmpeg on real files (read-only).

Usage: python3 tools/accuracy/real_files.py FILE...
"""
import json
import subprocess
import sys

import numpy as np

sys.path.insert(0, "src")
from qctrack.core.loudness import LoudnessAnalyzer  # noqa: E402
from qctrack.native.mpg123 import Mpg123Decoder  # noqa: E402
from qctrack.native.vorbisfile import VorbisDecoder  # noqa: E402


def probe(path: str) -> tuple[int, int, str]:
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate,channels,codec_name", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout)["streams"][0]
    return int(j["sample_rate"]), int(j["channels"]), j["codec_name"]


for path in sys.argv[1:]:
    rate, ch, codec = probe(path)
    if codec not in ("mp3", "vorbis"):
        print(f"{path.split('/')[-1]}: skipped ({codec})")
        continue
    dec = (Mpg123Decoder if codec == "mp3" else VorbisDecoder)(path)
    la = LoudnessAnalyzer(rate)
    peak = 0.0
    for b in dec.blocks():
        la.feed(b)
        peak = max(peak, float(np.abs(b).max()))
    r = la.results()[0]
    ff = subprocess.run(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                        capture_output=True, text=True).stderr
    tail = ff[ff.rfind("Summary"):]
    def val(key: str) -> float:
        line = [ln for ln in tail.splitlines() if ln.strip().startswith(key)]
        return float(line[0].split()[1]) if line else float("nan")
    print(f"{path.split('/')[-1][:28]:28s} {codec:6s} {rate} Hz  ours: I {r.integrated_lufs:6.2f} LUFS "
          f"SP {r.sample_peak_db:5.2f} TP {r.true_peak_db:5.2f}  ffmpeg: I {val('I:'):6.2f} TP {val('Peak:'):5.2f}")
