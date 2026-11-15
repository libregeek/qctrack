"""Build tests/golden/input-report.json, the saved measurements the golden tests rescore.

Run from the project root only when the measurement format changes on purpose:
    python3 tools/make_golden_input.py
Then regenerate the expected outputs with QCTRACK_UPDATE_GOLDEN=1 pytest tests/test_golden.py
and review the diff. Paths are rewritten to /golden/lib so the file does not depend on a
temporary folder.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "tests")
from helpers import encode  # noqa: E402
from qctrack.cli.main import main  # noqa: E402


def build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lib = Path(tmp) / "lib"
        lib.mkdir()
        encode(
            lib / "genuine.mp3",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            "-metadata",
            "title=Genuine",
            "-metadata",
            "artist=A. Artist",
            "-metadata",
            "album=Golden",
        )
        encode(
            lib / "fake320.mp3",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            "-metadata",
            'title=Band-limited, "quoted"',
            "-metadata",
            "album=Golden",
            broadband=False,
        )
        encode(lib / "vorbis.ogg", "-c:a", "libvorbis", "-q:a", "5", "-metadata", "title==formula")
        full = encode(lib / "cut.mp3", "-c:a", "libmp3lame", "-b:a", "128k")
        data = full.read_bytes()
        full.write_bytes(data[: len(data) * 2 // 3])
        (lib / "junk.mp3").write_bytes(b"not audio" * 100)
        encode(lib / "other.opus", "-c:a", "libopus", "-b:a", "96k")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=d=3",
                str(lib / "silence.mp3"),
            ],
            check=True,
        )
        out = Path(tmp) / "out"
        out.mkdir()
        assert main(["scan", str(lib), "--no-cache", "--silent", "--json", "-o", str(out)]) in (
            0,
            1,
        )
        text = (out / "report.json").read_text().replace(str(lib), "/golden/lib")
        doc = json.loads(text)
        Path("tests/golden/input-report.json").write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
        )
    print("wrote tests/golden/input-report.json")


if __name__ == "__main__":
    build()
