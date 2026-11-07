"""Capture what a C library writes to file descriptor 2.

libmpg123 reports concealed damage (broken frames, lost sync) only as text on stderr, never
through return codes, so F2.06 reads that text. The capture redirects fd 2 into a pipe
for the duration of one decoder call and restores it straight afterwards, so the caller's
own stderr output is never swallowed. No thread is needed: calls produce little text and the
pipe is drained after each one.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import sys
from collections.abc import Iterator

#: Lines kept in full; further lines are only counted.
MAX_LINES = 1000
_PIPE_BYTES = 1 << 20
_F_SETPIPE_SZ = 1031  # Linux; not exported by the fcntl module on every Python


class StderrCapture:
    def __init__(self, max_lines: int = MAX_LINES) -> None:
        self.lines: list[str] = []
        #: Lines read after :attr:`lines` reached its limit.
        self.dropped = 0
        self._max = max_lines
        self._partial = b""
        self._read, self._write = os.pipe()
        self._saved = os.dup(2)
        os.set_blocking(self._read, False)
        with contextlib.suppress(OSError):
            fcntl.fcntl(self._write, _F_SETPIPE_SZ, _PIPE_BYTES)

    @contextlib.contextmanager
    def active(self) -> Iterator[None]:
        """Send fd 2 to the pipe while the block runs."""
        sys.stderr.flush()
        os.dup2(self._write, 2)
        try:
            yield
        finally:
            os.dup2(self._saved, 2)
            self._drain()

    def _drain(self) -> None:
        while True:
            try:
                chunk = os.read(self._read, 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            self._partial += chunk
        *whole, self._partial = self._partial.split(b"\n")
        for raw in whole:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if len(self.lines) < self._max:
                self.lines.append(line)
            else:
                self.dropped += 1

    def close(self) -> None:
        if self._partial.strip():
            self.lines.append(self._partial.decode("utf-8", "replace").strip())
            self._partial = b""
        for fd in (self._read, self._write, self._saved):
            with contextlib.suppress(OSError):
                os.close(fd)
        self._read = self._write = self._saved = -1
