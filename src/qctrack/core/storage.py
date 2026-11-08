"""Choose how many workers to run from the kind of storage the files are on (REQ-NFR-16).

Many parallel readers suit an SSD but make a spinning disk seek back and forth, and a network
share is limited by its link. So the default is one worker per CPU core on solid-state storage,
and two on a spinning disk, a network share or anything we cannot identify (two, not one, so
decoding on one file overlaps the read of the next). ``--jobs`` always overrides this.

Detection uses ``/proc/self/mountinfo`` and ``/sys/dev/block``; both roots can be replaced for
tests. Only the files and folders named on the command line are examined.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Iterable
from pathlib import Path

SLOW_JOBS = 2

#: Filesystems that live across a network (or a user-space bridge to one).
_NETWORK_FS = frozenset(
    {
        "nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "ceph", "glusterfs", "afs", "lustre",
        "davfs", "sshfs", "fuse.sshfs", "fuse.rclone", "fuse.gvfsd-fuse", "fuse.s3fs",
        "fuse.davfs2", "fuse.smbnetfs", "fuse.curlftpfs", "ncpfs", "gpfs", "beegfs",
    }
)  # fmt: skip


#: Memory-backed filesystems: reading in parallel costs nothing.
_MEMORY_FS = frozenset({"tmpfs", "ramfs"})


class Storage(enum.Enum):
    SOLID_STATE = "solid-state"
    ROTATIONAL = "spinning disk"
    NETWORK = "network"
    UNKNOWN = "unknown"


def cpu_count() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # not Linux
        return max(1, os.cpu_count() or 1)


def default_jobs(kinds: Iterable[Storage], cpus: int | None = None) -> int:
    """One per core only if every location is solid-state; otherwise the slow setting."""
    cpus = cpus or cpu_count()
    if all(k is Storage.SOLID_STATE for k in kinds):
        return cpus
    return min(SLOW_JOBS, cpus)


def describe(kinds: Iterable[Storage]) -> str:
    names = sorted({k.value for k in kinds})
    return ", ".join(names) if names else "none"


def _unescape(field: str) -> str:
    r"""mountinfo writes space, tab, newline and backslash as octal escapes like ``\040``."""
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and field[i + 1 : i + 4].isdigit():
            out.append(chr(int(field[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


class _Mount:
    def __init__(self, dev: int, mount_point: str, fstype: str, source: str) -> None:
        self.dev, self.mount_point, self.fstype, self.source = dev, mount_point, fstype, source


def _read_mounts(mountinfo: Path) -> dict[int, _Mount]:
    """Mounts by ``st_dev``. When a device is mounted twice the first entry is kept."""
    mounts: dict[int, _Mount] = {}
    try:
        text = mountinfo.read_text(errors="replace")
    except OSError:
        return mounts
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        parts, tail = left.split(), right.split()
        if not sep or len(parts) < 5 or len(tail) < 2:
            continue
        try:
            major, minor = (int(x) for x in parts[2].split(":"))
        except ValueError:
            continue
        dev = os.makedev(major, minor)
        mounts.setdefault(dev, _Mount(dev, _unescape(parts[4]), tail[0], _unescape(tail[1])))
    return mounts


def _block_dir(sys_block: Path, dev: int) -> Path:
    return sys_block / f"{os.major(dev)}:{os.minor(dev)}"


def _is_rotational(sys_block: Path, dev: int, depth: int = 0) -> bool | None:
    """True/False for a block device, following partitions and device-mapper/RAID members.
    A stack is rotational if any member is. None when the device cannot be examined."""
    d = _block_dir(sys_block, dev)
    if depth > 8 or not d.exists():
        return None
    real = d.resolve()
    whole = real.parent if (real / "partition").exists() else real
    slaves = whole / "slaves"
    members = sorted(slaves.iterdir()) if slaves.is_dir() else []
    if members:
        found = [_is_rotational(sys_block, _devnum(m), depth + 1) for m in members]
        if any(f is True for f in found):
            return True
        return False if all(f is False for f in found) else None
    try:
        return (whole / "queue" / "rotational").read_text().strip() == "1"
    except OSError:
        return None


def _devnum(device_dir: Path) -> int:
    """Device number from the ``dev`` file (``major:minor``) of a sysfs block device."""
    try:
        major, minor = (int(x) for x in (device_dir / "dev").read_text().strip().split(":"))
    except (OSError, ValueError):
        return 0
    return os.makedev(major, minor)


def classify(
    path: Path,
    mountinfo: Path = Path("/proc/self/mountinfo"),
    sys_block: Path = Path("/sys/dev/block"),
) -> Storage:
    """The kind of storage ``path`` is on."""
    try:
        dev = os.stat(path).st_dev
    except OSError:
        return Storage.UNKNOWN
    mount = _read_mounts(mountinfo).get(dev)
    if mount is None:
        return Storage.UNKNOWN
    if mount.fstype in _NETWORK_FS or mount.fstype.startswith(("nfs", "fuse.sshfs")):
        return Storage.NETWORK
    if mount.fstype in _MEMORY_FS:
        return Storage.SOLID_STATE
    source_dev = dev
    if mount.source.startswith("/dev/"):  # btrfs, zfs and friends report an anonymous st_dev
        try:
            source_dev = os.stat(mount.source).st_rdev
        except OSError:
            return Storage.UNKNOWN
    rotational = _is_rotational(sys_block, source_dev)
    if rotational is None:
        return Storage.UNKNOWN
    return Storage.ROTATIONAL if rotational else Storage.SOLID_STATE
