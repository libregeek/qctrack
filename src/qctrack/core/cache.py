"""Result cache and resume (architecture 4.5, REQ-NFR-15, REQ-ACC-04).

An SQLite database in the application's own data directory, never beside the audio. One row
per file and scan mode, holding the *measurements* (never scores, so a profile change does not
invalidate anything). A row is reused only when the file's size and modification time and the
analyser version all still match. Each result is committed as it arrives, so a scan that is
interrupted resumes where it stopped. The cache is only a speed-up: if it cannot be opened or
read, the scan carries on without it.
"""

from __future__ import annotations

import os
import sqlite3
import zlib
from pathlib import Path
from types import TracebackType
from typing import Self

from qctrack.core.analyze import ANALYSER_VERSION, Measured
from qctrack.core.codec import measured_from_json, measured_to_json

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    path TEXT NOT NULL,
    quick INTEGER NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    version INTEGER NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (path, quick)
) WITHOUT ROWID
"""


class CacheError(Exception):
    """The cache could not be opened or used."""


def default_cache_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".local" / "share"
    return root / "qctrack" / "cache.db"


def file_key(path: Path) -> tuple[str, int, int] | None:
    """(absolute path, size, mtime in ns) of ``path``, or None if it cannot be examined."""
    try:
        st = path.stat()
    except OSError:
        return None
    return os.path.abspath(path), st.st_size, st.st_mtime_ns


def cacheable(measured: Measured) -> bool:
    """A read failure may be temporary (permissions, a network share), so it is never kept."""
    return not measured.error


class ResultCache:
    def __init__(self, db_path: Path | None = None) -> None:
        self.path = db_path or default_cache_path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, timeout=30)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute(_SCHEMA)
            self._db.commit()
        except (OSError, sqlite3.Error) as e:
            raise CacheError(f"cannot open cache {self.path}: {e}") from e

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
        self._db.close()

    def get(self, path: Path, quick: bool) -> Measured | None:
        """The cached measurements for ``path`` if the file is unchanged, else None."""
        key = file_key(path)
        if key is None:
            return None
        name, size, mtime_ns = key
        try:
            row = self._db.execute(
                "SELECT data FROM results WHERE path=? AND quick=? AND size=? "
                "AND mtime_ns=? AND version=?",
                (name, int(quick), size, mtime_ns, ANALYSER_VERSION),
            ).fetchone()
            if row is None:
                return None
            return measured_from_json(zlib.decompress(row[0]).decode(), path)
        except (sqlite3.Error, zlib.error, ValueError):
            return None  # unreadable entry: analyse the file again and overwrite it

    def put(self, key: tuple[str, int, int], quick: bool, measured: Measured) -> None:
        """Store ``measured`` under ``key``, taken *before* the file was analysed, so a file
        that changed during analysis is not mistaken for unchanged. Committed at once."""
        if not cacheable(measured):
            return
        name, size, mtime_ns = key
        data = zlib.compress(measured_to_json(measured).encode(), 3)
        try:
            with self._db:  # one transaction: committed whole or not at all
                self._db.execute(
                    "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?)",
                    (name, int(quick), size, mtime_ns, ANALYSER_VERSION, data),
                )
        except sqlite3.Error as e:
            raise CacheError(f"cannot write cache {self.path}: {e}") from e
