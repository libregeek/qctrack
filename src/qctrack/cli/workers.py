"""Worker processes: one bad file must cost one file (REQ-ANL-03, REQ-NFR-05).

Passes 1 and 2 run in worker processes started through ``multiprocessing``'s forkserver,
which preloads NumPy once. The parent gives each worker one file at a time and watches it:

* a worker that dies (a decoder crash) turns its file into an Error and is replaced;
* a worker that runs past the time budget is killed, and its file becomes an Error;
* each worker has an address-space limit (``RLIMIT_AS``), so a file that makes a decoder
  allocate wildly fails with MemoryError instead of exhausting the machine.

Workers are single-threaded (BLAS thread count 1): parallelism comes from the pool size.
"""

from __future__ import annotations

import multiprocessing
import os
import resource
import signal
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from multiprocessing.connection import Connection, wait
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from qctrack.core.analyze import Measured, measure_file

DEFAULT_TIMEOUT_S = 300.0
DEFAULT_MEMORY_MB = 2048

#: What a worker runs for each file. Replaced in tests to inject crashes and hangs.
TARGET: Callable[[Path, bool], Measured] = measure_file


@dataclass
class JobResult:
    index: int
    #: The measurements, or None when ``error`` says why there are none.
    measured: Measured | None = None
    error: str = ""


def _worker_main(
    conn: Connection, quick: bool, memory_mb: int, target: Callable[[Path, bool], Measured]
) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent handles Ctrl-C
    try:
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError):
        pass  # a limit we cannot set is better than no worker
    while True:
        try:
            path = conn.recv()
        except EOFError:
            return
        if path is None:
            return
        try:
            reply: tuple[str, Any] = ("ok", target(path, quick))
        except MemoryError:
            reply = ("error", f"out of memory (limit {memory_mb} MB)")
        except Exception as e:  # a bug must not take the worker down with a traceback
            reply = ("error", f"{type(e).__name__}: {e}")
        conn.send(reply)


class _Slot:
    """One worker process and the job it is running, if any."""

    def __init__(self) -> None:
        self.proc: multiprocessing.process.BaseProcess | None = None
        self.conn: Connection | None = None
        self.job: tuple[int, Path] | None = None
        self.deadline = 0.0

    def stop(self) -> None:
        if self.proc is not None:
            if self.proc.is_alive():
                self.proc.kill()
            self.proc.join()
            self.proc.close()
        if self.conn is not None:
            self.conn.close()
        self.proc = self.conn = self.job = None


def _death_reason(code: int | None) -> str:
    if code is not None and code < 0:
        try:
            return f"worker crashed ({signal.Signals(-code).name})"
        except ValueError:
            return f"worker crashed (signal {-code})"
    return f"worker exited unexpectedly (code {code})"


class WorkerPool:
    def __init__(
        self,
        jobs: int,
        *,
        quick: bool = False,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        memory_mb: int = DEFAULT_MEMORY_MB,
    ) -> None:
        for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ.setdefault(var, "1")
        self._ctx = multiprocessing.get_context("forkserver")
        self._ctx.set_forkserver_preload(["numpy", "qctrack.core.analyze"])
        self._jobs = max(1, jobs)
        self._quick = quick
        self._timeout = timeout_s
        self._memory_mb = memory_mb
        self._slots = [_Slot() for _ in range(self._jobs)]

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        for slot in self._slots:
            if slot.conn is not None and slot.job is None:
                try:
                    slot.conn.send(None)  # ask an idle worker to leave
                except (OSError, ValueError):
                    pass
            slot.stop()

    def _start(self, slot: _Slot) -> None:
        parent, child = self._ctx.Pipe()
        proc = self._ctx.Process(
            target=_worker_main,
            args=(child, self._quick, self._memory_mb, TARGET),
            daemon=True,
        )
        proc.start()
        child.close()
        slot.proc, slot.conn = proc, parent

    def _dispatch(self, slot: _Slot, index: int, path: Path) -> None:
        if slot.proc is None:
            self._start(slot)
        assert slot.conn is not None
        slot.job = (index, path)
        slot.deadline = time.monotonic() + self._timeout
        slot.conn.send(path)

    def run(
        self, paths: Sequence[Path], on_start: Callable[[int, Path], None] | None = None
    ) -> Iterator[JobResult]:
        """Analyse ``paths``, yielding each result as it completes (not in input order).

        ``on_start(n, path)`` is called as the n-th file (1-based) is handed to a worker.
        """
        pending = deque(enumerate(paths))
        started = 0
        while True:
            for slot in self._slots:
                if slot.job is None and pending:
                    index, path = pending.popleft()
                    started += 1
                    if on_start:
                        on_start(started, path)
                    self._dispatch(slot, index, path)
            busy = [s for s in self._slots if s.job is not None]
            if not busy:
                return
            wait_for = max(0.0, min(s.deadline for s in busy) - time.monotonic())
            watched: list[Any] = [s.conn for s in busy] + [s.proc.sentinel for s in busy if s.proc]
            wait(watched, timeout=wait_for)
            for slot in busy:
                result = self._collect(slot)
                if result is not None:
                    yield result

    def _collect(self, slot: _Slot) -> JobResult | None:
        """The result of ``slot``'s job if it has finished, died or timed out."""
        assert slot.conn is not None and slot.proc is not None and slot.job is not None
        index = slot.job[0]
        if slot.conn.poll():  # an answer wins over a death that came right after it
            try:
                kind, payload = slot.conn.recv()
            except (EOFError, OSError):
                pass
            else:
                slot.job = None
                if kind == "ok":
                    return JobResult(index, measured=payload)
                return JobResult(index, error=payload)
        elif slot.proc.is_alive():
            if time.monotonic() < slot.deadline:
                return None
            slot.stop()
            return JobResult(index, error=f"timed out after {self._timeout:g} s")
        slot.proc.join(1.0)  # let the exit code settle before reading it
        reason = _death_reason(slot.proc.exitcode)
        slot.stop()
        return JobResult(index, error=reason)
