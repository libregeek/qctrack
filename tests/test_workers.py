"""Worker isolation (todo 4.3): a crashing, hanging or memory-hungry file costs one file."""

import os
import signal
import time
from pathlib import Path

import pytest

from helpers import encode
from qctrack.cli import workers
from qctrack.cli.main import main
from qctrack.cli.workers import JobResult, WorkerPool
from qctrack.core.analyze import Measured, measure_file


def fault_injecting_target(path: Path, quick: bool) -> Measured:
    """Behave according to the file name, so one scan can mix good and bad files."""
    name = path.name
    if name.startswith("crash"):
        os.kill(os.getpid(), signal.SIGSEGV)
    if name.startswith("hang"):
        time.sleep(3600)
    if name.startswith("hog"):
        _ = bytearray(8 * 1024**3)  # more than the address-space limit allows
    if name.startswith("boom"):
        raise RuntimeError("bug in an analyser")
    return measure_file(path, quick)


@pytest.fixture(autouse=True)
def inject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workers, "TARGET", fault_injecting_target)


@pytest.fixture(scope="module")
def good_mp3(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return encode(tmp_path_factory.mktemp("w") / "good.mp3", "-c:a", "libmp3lame", "-b:a", "192k")


def make_files(tmp_path: Path, good: Path, names: list[str]) -> list[Path]:
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(good.read_bytes())
        out.append(p)
    return out


def run_all(files: list[Path], **kw: object) -> dict[str, JobResult]:
    with WorkerPool(**kw) as pool:  # type: ignore[arg-type]
        results = {files[r.index].name: r for r in pool.run(files)}
    assert len(results) == len(files)
    return results


def test_a_crash_costs_one_file_and_the_worker_is_replaced(tmp_path: Path, good_mp3: Path) -> None:
    files = make_files(tmp_path, good_mp3, ["a.mp3", "crash1.mp3", "b.mp3", "crash2.mp3", "c.mp3"])
    r = run_all(files, jobs=1)
    assert r["crash1.mp3"].error == "worker crashed (SIGSEGV)"
    assert r["crash2.mp3"].error == "worker crashed (SIGSEGV)"
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        assert r[name].measured is not None and r[name].error == ""


def test_a_hanging_file_is_killed_at_the_time_budget(tmp_path: Path, good_mp3: Path) -> None:
    files = make_files(tmp_path, good_mp3, ["hang.mp3", "after.mp3"])
    start = time.monotonic()
    r = run_all(files, jobs=1, timeout_s=3.0)
    assert time.monotonic() - start < 20
    assert r["hang.mp3"].error == "timed out after 3 s"
    assert r["after.mp3"].measured is not None


def test_a_memory_hog_is_stopped_by_the_address_space_limit(tmp_path: Path, good_mp3: Path) -> None:
    files = make_files(tmp_path, good_mp3, ["hog.mp3", "after.mp3"])
    r = run_all(files, jobs=1, memory_mb=2048)
    assert "out of memory" in r["hog.mp3"].error
    assert r["after.mp3"].measured is not None


def test_an_exception_in_the_analysis_is_an_error_and_the_worker_survives(
    tmp_path: Path, good_mp3: Path
) -> None:
    files = make_files(tmp_path, good_mp3, ["boom.mp3", "after.mp3"])
    r = run_all(files, jobs=1)
    assert r["boom.mp3"].error == "RuntimeError: bug in an analyser"
    assert r["after.mp3"].measured is not None


def test_several_workers_finish_everything_despite_failures(tmp_path: Path, good_mp3: Path) -> None:
    names = [f"ok{i}.mp3" for i in range(6)] + ["crash.mp3", "hang.mp3", "boom.mp3"]
    r = run_all(make_files(tmp_path, good_mp3, names), jobs=3, timeout_s=4.0)
    assert sum(1 for x in r.values() if x.measured is not None) == 6
    assert {n for n, x in r.items() if x.error} == {"crash.mp3", "hang.mp3", "boom.mp3"}


def test_on_start_reports_each_file_in_order(tmp_path: Path, good_mp3: Path) -> None:
    files = make_files(tmp_path, good_mp3, ["a.mp3", "b.mp3", "c.mp3"])
    seen: list[tuple[int, str]] = []
    with WorkerPool(jobs=1) as pool:
        list(pool.run(files, lambda n, p: seen.append((n, p.name))))
    assert seen == [(1, "a.mp3"), (2, "b.mp3"), (3, "c.mp3")]


def test_workers_are_gone_after_the_pool_closes(tmp_path: Path, good_mp3: Path) -> None:
    files = make_files(tmp_path, good_mp3, ["a.mp3", "hang.mp3"])
    with WorkerPool(jobs=2, timeout_s=2.0) as pool:
        list(pool.run(files))
        procs = [s.proc for s in pool._slots if s.proc is not None]
    assert all(s.proc is None for s in pool._slots) and procs  # processes were released


def test_scan_reports_failures_as_error_rows_and_finishes_the_rest(
    tmp_path: Path, good_mp3: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = make_files(tmp_path, good_mp3, ["a.mp3", "crash.mp3", "hang.mp3", "z.mp3"])
    argv = ["scan", "--timeout", "3", "-j", "2", *map(str, files)]
    assert main(argv) == 1
    out = capsys.readouterr().out
    rows = {line.split()[0].rsplit("/", 1)[-1]: line for line in out.splitlines() if "mp3" in line}
    assert "Error" in rows["crash.mp3"] and "SIGSEGV" in rows["crash.mp3"]
    assert "Error" in rows["hang.mp3"] and "timed out after 3 s" in rows["hang.mp3"]
    assert "Error" not in rows["a.mp3"] and "Error" not in rows["z.mp3"]
    summary = next(line for line in out.splitlines() if line.startswith("4 files:"))
    assert "errors 2" in summary
    assert list(rows) == ["a.mp3", "crash.mp3", "hang.mp3", "z.mp3"]  # input order kept


@pytest.mark.parametrize("argv", [["-j", "0"], ["-j", "x"], ["--timeout", "0"], ["--timeout", "x"]])
def test_bad_jobs_and_timeout_are_usage_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["scan", *argv, "x.mp3"])
    assert exc.value.code == 2
