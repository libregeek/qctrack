"""Storage-aware --jobs default, --quick and --follow-links (todo 4.5)."""

import os
from pathlib import Path

import pytest

from helpers import encode
from qctrack.cli import main as cli
from qctrack.cli.main import discover, main
from qctrack.core.storage import Storage, classify, default_jobs, describe


def fake_system(
    tmp: Path, target: Path, fstype: str, rotational: str | None, dm: bool = False
) -> tuple[Path, Path]:
    """A mountinfo and a /sys/dev/block that describe the device ``target`` lives on."""
    dev = os.stat(target).st_dev
    mm = f"{os.major(dev)}:{os.minor(dev)}"
    mountinfo = tmp / "mountinfo"
    mountinfo.write_text(f"36 1 {mm} / {target} rw - {fstype} fakesrc rw\n")
    sysblock = tmp / "sys" / "dev" / "block"
    sysblock.mkdir(parents=True)
    if rotational is not None:
        disk = tmp / "sys" / "devices" / "disk0"
        (disk / "queue").mkdir(parents=True)
        (disk / "queue" / "rotational").write_text(rotational + "\n")
        (disk / "dev").write_text("8:0\n")
        if dm:
            top = tmp / "sys" / "devices" / "dm-0"
            (top / "slaves").mkdir(parents=True)
            (top / "slaves" / "disk0").symlink_to(disk)
            (top / "dev").write_text(mm + "\n")
            (sysblock / mm).symlink_to(top)
            (sysblock / "8:0").symlink_to(disk)  # the member the slaves/ entry points at
        else:
            (sysblock / mm).symlink_to(disk)
    return mountinfo, sysblock


@pytest.mark.parametrize(
    ("fstype", "rotational", "dm", "expected"),
    [
        ("ext4", "0", False, Storage.SOLID_STATE),
        ("ext4", "1", False, Storage.ROTATIONAL),
        ("ext4", "1", True, Storage.ROTATIONAL),  # LVM on a spinning disk
        ("ext4", "0", True, Storage.SOLID_STATE),
        ("nfs4", "0", False, Storage.NETWORK),
        ("cifs", None, False, Storage.NETWORK),
        ("fuse.sshfs", None, False, Storage.NETWORK),
        ("tmpfs", None, False, Storage.SOLID_STATE),
        ("ext4", None, False, Storage.UNKNOWN),  # no sysfs entry
    ],
)
def test_classify(
    tmp_path: Path, fstype: str, rotational: str | None, dm: bool, expected: Storage
) -> None:
    folder = tmp_path / "music"
    folder.mkdir()
    mountinfo, sysblock = fake_system(tmp_path, folder, fstype, rotational, dm)
    assert classify(folder, mountinfo, sysblock) is expected


def test_unknown_when_mountinfo_is_missing(tmp_path: Path) -> None:
    assert classify(tmp_path, tmp_path / "none", tmp_path / "none") is Storage.UNKNOWN
    assert classify(tmp_path / "gone") is Storage.UNKNOWN


def test_real_system_gives_an_answer(tmp_path: Path) -> None:
    assert isinstance(classify(tmp_path), Storage)  # never raises, whatever the machine


def test_default_jobs() -> None:
    ssd, hdd, net, unk = (Storage.SOLID_STATE, Storage.ROTATIONAL, Storage.NETWORK, Storage.UNKNOWN)
    assert default_jobs([ssd], cpus=8) == 8
    assert default_jobs([ssd, ssd], cpus=8) == 8
    assert default_jobs([hdd], cpus=8) == 2
    assert default_jobs([net], cpus=8) == 2
    assert default_jobs([unk], cpus=8) == 2
    assert default_jobs([ssd, hdd], cpus=8) == 2  # the slowest location decides
    assert default_jobs([hdd], cpus=1) == 1
    assert default_jobs([ssd], cpus=1) == 1
    assert describe([ssd, hdd, ssd]) == "solid-state, spinning disk"


def used_workers(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seen: list[int] = []
    real = cli.WorkerPool  # type: ignore[attr-defined]

    def spy(jobs: int, **kw: object) -> object:
        seen.append(jobs)
        return real(jobs, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "WorkerPool", spy)
    return seen


@pytest.fixture(scope="module")
def mp3(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return encode(tmp_path_factory.mktemp("s") / "a.mp3", "-c:a", "libmp3lame", "-b:a", "192k")


def test_scan_uses_detected_default_and_jobs_overrides(
    mp3: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = used_workers(monkeypatch)
    monkeypatch.setattr(cli, "classify", lambda p: Storage.ROTATIONAL)
    assert main(["scan", "-v", "--no-cache", str(mp3)]) == 0
    assert "2 worker(s), storage: spinning disk" in capsys.readouterr().err
    monkeypatch.setattr(cli, "classify", lambda p: Storage.SOLID_STATE)
    monkeypatch.setattr("qctrack.core.storage.cpu_count", lambda: 6)
    assert main(["scan", "--no-cache", str(mp3)]) == 0
    assert main(["scan", "--no-cache", "-j", "1", str(mp3)]) == 0
    assert seen == [2, 6, 1]


def test_quick_marks_ratings_provisional(mp3: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", "--quick", "--no-cache", "-j", "1", str(mp3)]) == 0
    out = capsys.readouterr().out
    assert "* provisional" in out
    assert main(["scan", "--no-cache", "-j", "1", str(mp3)]) == 0
    assert "* provisional" not in capsys.readouterr().out


@pytest.fixture
def linked(tmp_path: Path) -> Path:
    root = tmp_path / "lib"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.mp3").write_bytes(b"x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.mp3").write_bytes(b"x")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "real" / "loop").symlink_to(root, target_is_directory=True)
    return root


def names(root: Path, follow: bool) -> list[str]:
    return [str(p.relative_to(root)) for p in discover([root], follow)]


def test_links_to_folders_are_not_followed_by_default(linked: Path) -> None:
    assert names(linked, False) == ["real/a.mp3"]


def test_follow_links_enters_links_and_survives_loops(linked: Path) -> None:
    found = names(linked, True)
    assert "link/b.mp3" in found
    assert "real/a.mp3" in found
    assert len(found) == len(set(found)) <= 3  # the loop back to lib/ adds nothing endless
