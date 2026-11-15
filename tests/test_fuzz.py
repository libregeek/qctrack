"""Fuzz and corrupt-file tests (todo 6.2, REQ-ACC-*, REQ-NFR robustness).

Damaged files must never crash or hang the scan: every file gets a row, the exit code is a
normal one, and no worker dies. A worker crash or timeout would mean a native decoder fault
that the worker isolation hid, so these tests treat "worker crashed" as a failure.
"""

import json
import random
from pathlib import Path
from typing import Any

import pytest

from helpers import encode
from qctrack.cli.main import main

MUTANTS_PER_FORMAT = 40
SEED = 20261115


def mutate(data: bytes, rng: random.Random) -> bytes:
    """One random kind of damage, chosen by ``rng``."""
    b = bytearray(data)
    kind = rng.choice(
        ["flip", "flip_many", "truncate", "zero", "noise", "delete", "duplicate", "prepend", "head"]
    )
    n = len(b)
    if kind == "flip":
        b[rng.randrange(n)] ^= 1 << rng.randrange(8)
    elif kind == "flip_many":
        for _ in range(rng.randint(10, 500)):
            b[rng.randrange(n)] ^= 1 << rng.randrange(8)
    elif kind == "truncate":
        del b[rng.randrange(1, n) :]
    elif kind == "zero":
        s = rng.randrange(n)
        b[s : s + rng.randint(1, 20000)] = bytes(min(rng.randint(1, 20000), n - s))
    elif kind == "noise":
        s = rng.randrange(n)
        length = min(rng.randint(1, 20000), n - s)
        b[s : s + length] = rng.randbytes(length)
    elif kind == "delete":
        s = rng.randrange(n)
        del b[s : s + rng.randint(1, 20000)]
    elif kind == "duplicate":
        s = rng.randrange(n)
        chunk = bytes(b[s : s + rng.randint(1, 20000)])
        b[s:s] = chunk
    elif kind == "prepend":
        b[0:0] = rng.randbytes(rng.randint(1, 5000))
    else:  # damage only the first bytes, where headers live
        for _ in range(rng.randint(1, 16)):
            b[rng.randrange(min(n, 200))] = rng.randrange(256)
    return bytes(b)


@pytest.fixture(scope="module")
def mutants(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("fuzz")
    base = tmp_path_factory.mktemp("fuzzbase")
    sources = {
        "mp3": encode(base / "a.mp3", "-c:a", "libmp3lame", "-b:a", "128k"),
        "ogg": encode(base / "a.ogg", "-c:a", "libvorbis", "-q:a", "4"),
    }
    rng = random.Random(SEED)
    for ext, src in sources.items():
        data = src.read_bytes()
        for i in range(MUTANTS_PER_FORMAT):
            damaged = data
            for _ in range(rng.choice([1, 1, 2, 3])):
                damaged = mutate(damaged, rng)
            (root / f"m{i:02d}.{ext}").write_bytes(damaged)
    return root


def test_damaged_files_never_crash_or_hang_the_scan(
    mutants: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = sorted(p.name for p in mutants.iterdir())
    assert len(files) == 2 * MUTANTS_PER_FORMAT
    code = main(["scan", str(mutants), "--no-cache", "-j", "4", "--timeout", "60", "--json=-"])
    out = capsys.readouterr().out
    assert code in (0, 1), f"exit code {code}"
    doc: dict[str, Any] = json.loads(out)
    rows = {Path(t["path"]).name: t for t in doc["tracks"]}
    assert sorted(rows) == files, "every file must get exactly one row"
    bad = {
        name: t["message"]
        for name, t in rows.items()
        if "crashed" in t.get("message", "") or "timed out" in t.get("message", "")
    }
    assert not bad, f"native decoder fault or hang hidden by worker isolation: {bad}"


def test_damaged_files_get_a_status_and_no_traceback(mutants: Path, tmp_path: Path) -> None:
    """Every row is Scored, Provisional, Unsupported or Error, never missing data."""
    out = tmp_path / "r"
    out.mkdir()
    args = ["scan", str(mutants), "--no-cache", "--silent", "--json", "-o", str(out)]
    assert main(args) in (0, 1)
    doc = json.loads((out / "report.json").read_text())
    for t in doc["tracks"]:
        assert t["status"] in {"Scored", "Provisional", "Unsupported", "Unscored", "Error"}, t
        if t["status"] == "Error":
            assert t["message"], t
