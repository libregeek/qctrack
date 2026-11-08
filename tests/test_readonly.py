"""Read-only guarantee (todo 4.6, REQ-ACC-01..04): a scan never changes the analysed files.

Three independent checks:
* the bytes, size, modification time and mode of every file are identical afterwards, and the
  folder holds no new file;
* a Python audit hook sees no open-for-write of anything under the library while the analysis
  code runs (this covers the Python side, including mutagen);
* the whole scan, native decoders included, still succeeds when the library is made
  read-only, so any attempt to write there would fail.
"""

import hashlib
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers import encode
from qctrack.cli.main import main
from qctrack.core.analyze import measure_file

WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def snapshot(root: Path) -> dict[str, tuple[str, int, int, int]]:
    """Per file: SHA-256, size, modification time (ns) and mode. Folders are listed too."""
    out: dict[str, tuple[str, int, int, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
        out[str(p.relative_to(root))] = (digest, st.st_size, st.st_mtime_ns, st.st_mode)
    return out


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("lib")
    mp3 = encode(root / "a.mp3", "-c:a", "libmp3lame", "-b:a", "192k")
    ogg = encode(root / "b.ogg", "-c:a", "libvorbis", "-q:a", "5")
    encode(root / "c.flac", "-c:a", "flac")  # unsupported in this release
    (root / "sub").mkdir()
    data = bytearray(mp3.read_bytes())
    data[20000:20040] = bytes(40)
    (root / "sub" / "damaged.mp3").write_bytes(data)  # decoder conceals damage
    (root / "sub" / "truncated.ogg").write_bytes(ogg.read_bytes()[:-5000])
    (root / "sub" / "junk.mp3").write_bytes(b"not audio at all" * 50)
    (root / "sub" / "empty.mp3").write_bytes(b"")
    return root


def test_scan_leaves_every_file_untouched(library: Path) -> None:
    before = snapshot(library)
    assert main(["scan", "-v", "-j", "2", str(library)]) == 1  # junk and flac are not scored
    assert main(["scan", "--quick", "--no-cache", str(library)]) == 1
    assert snapshot(library) == before


class WriteWatch:
    """Records every open-for-write under ``prefix`` while ``prefix`` is set."""

    def __init__(self) -> None:
        self.prefix = ""
        self.attempts: list[str] = []

    def __call__(self, event: str, args: tuple[object, ...]) -> None:
        if event != "open" or not self.prefix:
            return
        path, _mode, flags = args
        if (
            isinstance(path, str | os.PathLike)
            and str(path).startswith(self.prefix)
            and isinstance(flags, int)
            and flags & WRITE_FLAGS
        ):
            self.attempts.append(f"{path} flags={flags:#o}")


_watch = WriteWatch()
sys.addaudithook(_watch)  # audit hooks cannot be removed, so ``prefix`` switches it off


@pytest.fixture
def watch() -> Iterator[WriteWatch]:
    _watch.attempts.clear()
    yield _watch
    _watch.prefix = ""


def test_the_watch_itself_catches_a_write(library: Path, watch: WriteWatch) -> None:
    watch.prefix = str(library)
    (library / "probe.tmp").write_bytes(b"x")
    (library / "probe.tmp").unlink()
    assert len(watch.attempts) == 1


def test_analysis_code_never_opens_for_writing(library: Path, watch: WriteWatch) -> None:
    watch.prefix = str(library)
    for p in sorted(library.rglob("*")):
        if p.is_file():
            measure_file(p)
            measure_file(p, quick=True)
    assert watch.attempts == []


def test_scan_succeeds_when_the_library_is_read_only(library: Path, tmp_path: Path) -> None:
    copy = tmp_path / "ro"
    copy.mkdir()
    for p in sorted(library.rglob("*")):
        dest = copy / p.relative_to(library)
        if p.is_dir():
            dest.mkdir()
        else:
            dest.write_bytes(p.read_bytes())
    for p in sorted(copy.rglob("*"), reverse=True):
        p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    copy.chmod(copy.stat().st_mode & ~stat.S_IWUSR)
    try:
        if os.access(copy, os.W_OK):
            pytest.skip("running as a user that ignores file permissions")
        before = snapshot(copy)
        assert main(["scan", "-j", "2", str(copy)]) == 1
        assert snapshot(copy) == before
    finally:
        for p in [copy, *copy.rglob("*")]:
            p.chmod(p.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
