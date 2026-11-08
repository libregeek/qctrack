"""Result cache and resume (todo 4.4)."""

import os
import sqlite3
import time
from pathlib import Path

import pytest

import qctrack.cli.main as cli
from helpers import encode
from qctrack.cli import workers
from qctrack.cli.main import main
from qctrack.core import analyze
from qctrack.core.analyze import Measured, measure_file
from qctrack.core.cache import ResultCache, default_cache_path, file_key
from qctrack.core.codec import measured_from_json, measured_to_json


def logging_target(path: Path, quick: bool) -> Measured:
    """Runs in a worker: records which files were really analysed (next to the folder)."""
    with open(path.parent.parent / "analysed.log", "a") as log:
        log.write(path.name + "\n")
    return measure_file(path, quick)


@pytest.fixture(scope="module")
def mp3(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return encode(tmp_path_factory.mktemp("c") / "a.mp3", "-c:a", "libmp3lame", "-b:a", "192k")


@pytest.fixture(scope="module")
def ogg(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return encode(tmp_path_factory.mktemp("o") / "a.ogg", "-c:a", "libvorbis", "-q:a", "5")


@pytest.mark.parametrize("fixture", ["mp3", "ogg"])
def test_measurements_round_trip_exactly(fixture: str, request: pytest.FixtureRequest) -> None:
    path: Path = request.getfixturevalue(fixture)
    m = measure_file(path)
    assert repr(measured_from_json(measured_to_json(m), path)) == repr(m)


def test_garbage_is_rejected_not_crashed() -> None:
    with pytest.raises(ValueError):
        measured_from_json('{"detection": 5}', Path("x"))


def test_cache_lives_in_data_home_not_beside_audio(mp3: Path, tmp_path: Path) -> None:
    folder = tmp_path / "lib"
    folder.mkdir()
    (folder / "a.mp3").write_bytes(mp3.read_bytes())
    before = sorted(os.listdir(folder))
    assert main(["scan", str(folder)]) == 0
    assert sorted(os.listdir(folder)) == before
    assert default_cache_path().is_file()
    assert default_cache_path().parent.name == "qctrack"


def test_second_scan_reads_the_cache_and_gives_the_same_output(
    mp3: Path, ogg: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["scan", "-v", str(mp3), str(ogg)]) == 0
    first = capsys.readouterr().out

    def forbidden(path: Path, quick: bool) -> Measured:
        raise AssertionError("analysed again")

    monkeypatch.setattr(workers, "TARGET", forbidden)
    assert main(["scan", "-v", str(mp3), str(ogg)]) == 0
    assert capsys.readouterr().out == first


def test_changed_file_is_analysed_again(mp3: Path, tmp_path: Path) -> None:
    f = tmp_path / "a.mp3"
    f.write_bytes(mp3.read_bytes())
    assert main(["scan", str(f)]) == 0
    with ResultCache() as cache:
        assert cache.get(f, False) is not None
        st = f.stat()
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        assert cache.get(f, False) is None  # same size, new modification time
        f.write_bytes(mp3.read_bytes() + b"\0")
        assert cache.get(f, False) is None  # new size


def test_new_analyser_version_ignores_old_rows(mp3: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["scan", str(mp3)]) == 0
    with ResultCache() as cache:
        assert cache.get(mp3, False) is not None
        monkeypatch.setattr("qctrack.core.cache.ANALYSER_VERSION", 2)
        assert cache.get(mp3, False) is None


def test_quick_and_full_results_are_kept_apart(mp3: Path) -> None:
    assert main(["scan", "--quick", str(mp3)]) == 0
    with ResultCache() as cache:
        assert cache.get(mp3, True) is not None
        assert cache.get(mp3, False) is None


def test_profile_change_does_not_need_a_new_analysis(
    mp3: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["scan", str(mp3)]) == 0
    monkeypatch.setattr(workers, "TARGET", lambda p, q: (_ for _ in ()).throw(AssertionError))
    assert main(["scan", "--fail-below", "99.9", str(mp3)]) == 4


def test_read_errors_are_not_cached(tmp_path: Path) -> None:
    missing = tmp_path / "gone.mp3"
    assert main(["scan", str(missing)]) == 1
    with ResultCache() as cache:
        assert file_key(missing) is None
        assert cache.get(missing, False) is None
    m = Measured(missing, error="Permission denied")
    with ResultCache() as cache:
        cache.put(("/x", 1, 1), False, m)
        assert cache.get(Path("/x"), False) is None


def test_no_cache_option_neither_reads_nor_writes(mp3: Path) -> None:
    assert main(["scan", "--no-cache", str(mp3)]) == 0
    assert not default_cache_path().exists()


def test_corrupt_cache_file_warns_and_scan_still_works(
    mp3: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = default_cache_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a database" * 100)
    assert main(["scan", str(mp3)]) == 0
    captured = capsys.readouterr()
    assert "continuing without the cache" in captured.err
    assert "1 files" in captured.out


def test_damaged_row_is_replaced(mp3: Path) -> None:
    assert main(["scan", str(mp3)]) == 0
    db = sqlite3.connect(default_cache_path())
    db.execute("UPDATE results SET data = x'0102'")
    db.commit()
    db.close()
    with ResultCache() as cache:
        assert cache.get(mp3, False) is None
    assert main(["scan", str(mp3)]) == 0
    with ResultCache() as cache:
        assert cache.get(mp3, False) is not None


def test_interrupted_scan_resumes(
    mp3: Path,
    ogg: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = tmp_path / "lib"
    folder.mkdir()
    for i in range(4):
        (folder / f"t{i}.mp3").write_bytes(mp3.read_bytes())
        os.utime(folder / f"t{i}.mp3", (1_000_000 + i, 1_000_000 + i))
    real = analyze.score_measured
    seen: list[int] = []

    def interrupt_on_third(measured: Measured, profile: object) -> object:
        seen.append(1)
        if len(seen) == 3:
            raise KeyboardInterrupt
        return real(measured, profile)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "score_measured", interrupt_on_third)
    assert main(["scan", str(folder)]) == 130
    assert "run the same command again to resume" in capsys.readouterr().err
    monkeypatch.setattr(cli, "score_measured", real)
    with ResultCache() as cache:
        done = [p for p in sorted(folder.iterdir()) if cache.get(p, False) is not None]
    assert 1 <= len(done) < 4

    monkeypatch.setattr(workers, "TARGET", logging_target)
    assert main(["scan", str(folder)]) == 0
    analysed = (tmp_path / "analysed.log").read_text().split()
    assert len(analysed) == 4 - len(done)
    assert not set(analysed) & {p.name for p in done}
    assert "4 files" in capsys.readouterr().out


def test_second_scan_is_much_faster(mp3: Path, tmp_path: Path) -> None:
    folder = tmp_path / "lib"
    folder.mkdir()
    for i in range(3):
        (folder / f"t{i}.mp3").write_bytes(mp3.read_bytes())
    t0 = time.monotonic()
    main(["scan", str(folder)])
    cold = time.monotonic() - t0
    t0 = time.monotonic()
    main(["scan", str(folder)])
    warm = time.monotonic() - t0
    assert warm < cold / 3
