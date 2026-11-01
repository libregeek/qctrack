"""Resource tests: handles are freed (todo 1.4) and memory stays bounded (todo 1.5)."""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from qctrack.native.mpg123 import Mpg123Decoder, Mpg123Error
from qctrack.native.vorbisfile import VorbisDecoder, VorbisError

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")

ITERATIONS = 200
PAGE = os.sysconf("SC_PAGE_SIZE")


def rss_mb() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * PAGE / 1e6


def open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def run_leak_check(action: Callable[[], None]) -> tuple[int, float]:
    """Run ``action`` many times; return (fd growth, RSS growth in MB after warm-up)."""
    for _ in range(20):  # warm-up: allocator pools, caches, library tables
        action()
    fds0, rss0 = open_fds(), rss_mb()
    for _ in range(ITERATIONS):
        action()
    return open_fds() - fds0, rss_mb() - rss0


def partial_mp3(path: Path) -> None:
    with Mpg123Decoder(path) as d:
        for i, _ in enumerate(d.blocks()):
            if i == 20:
                break  # abandon mid-stream: the context manager must still free everything


def partial_ogg(path: Path) -> None:
    with VorbisDecoder(path) as d:
        for i, _ in enumerate(d.blocks()):
            if i == 20:
                break


@real
def test_mp3_handles_freed_when_abandoned_mid_stream() -> None:
    fds, mb = run_leak_check(lambda: partial_mp3(SAMPLES / "sample.mp3"))
    print(f"mp3 abandoned x{ITERATIONS}: fd growth {fds}, RSS growth {mb:.1f} MB")
    assert fds == 0
    assert mb < 5


@real
def test_ogg_handles_freed_when_abandoned_mid_stream() -> None:
    fds, mb = run_leak_check(lambda: partial_ogg(SAMPLES / "sample.ogg"))
    print(f"ogg abandoned x{ITERATIONS}: fd growth {fds}, RSS growth {mb:.1f} MB")
    assert fds == 0
    assert mb < 5


@real
def test_handles_freed_after_decode_errors(tmp_path: Path) -> None:
    junk = tmp_path / "junk.bin"
    junk.write_bytes(os.urandom(20_000))

    def failing() -> None:
        with pytest.raises(Mpg123Error), Mpg123Decoder(junk) as d:
            list(d.blocks())
        with pytest.raises(VorbisError):
            VorbisDecoder(junk)
        with pytest.raises(VorbisError):
            VorbisDecoder(SAMPLES / "sample.mp3")

    fds, mb = run_leak_check(failing)
    print(f"errors x{ITERATIONS}: fd growth {fds}, RSS growth {mb:.1f} MB")
    assert fds == 0
    assert mb < 5


@real
def test_repeated_full_decodes_do_not_grow_memory() -> None:
    def full() -> None:
        with Mpg123Decoder(SAMPLES / "Track9.mp3") as d:
            for _ in d.blocks():
                pass
        with VorbisDecoder(SAMPLES / "sample.ogg") as o:
            for _ in o.blocks():
                pass

    for _ in range(2):
        full()
    fds0, rss0 = open_fds(), rss_mb()
    for _ in range(10):
        full()
    print(f"full decodes x10: fd growth {open_fds() - fds0}, RSS growth {rss_mb() - rss0:.1f} MB")
    assert open_fds() == fds0
    assert rss_mb() - rss0 < 10


_DECODE_SCRIPT = """
import resource
import sys

sys.path.insert(0, sys.argv[2])
import numpy as np
from qctrack.native.mpg123 import Mpg123Decoder
from qctrack.native.vorbisfile import VorbisDecoder

path = sys.argv[1]
cls = VorbisDecoder if path.endswith('.ogg') else Mpg123Decoder
with cls(path) as d:
    for b in d.blocks():
        np.abs(b).max()
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
"""


@real
@pytest.mark.parametrize("name", ["sample.mp3", "Track9.mp3", "sample.ogg"])
def test_peak_memory_of_full_decode_is_under_150_mb(name: str) -> None:
    out = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _DECODE_SCRIPT,
            str(SAMPLES / name),
            str(Path("src").resolve()),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    peak_mb = float(out.stdout)
    print(f"{name}: peak RSS of the decoding process {peak_mb:.0f} MB")
    assert peak_mb < 150
