"""CSV export (requirements 6.2, 6.3).

RFC 4180 through the standard ``csv`` module: UTF-8, comma delimiter, header row, CRLF line
endings, fields quoted when needed. Every file is a row, including Unsupported and Error ones.
Not-evaluated and not-applicable values are empty cells, never 0. Text that a spreadsheet
could read as a formula is prefixed with an apostrophe. The factor columns follow the
profile's catalogue, so files from different scans line up.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime
from typing import TextIO

from qctrack import __version__
from qctrack.core.analyze import TrackReport
from qctrack.core.profile import OBJECTIVES, Profile

_IDENTITY = ["album", "title", "artist", "track_number", "file_path", "file_size_bytes"]
_FORMULA_START = ("=", "+", "-", "@", "−", "\t", "\r")


def safe(text: str) -> str:
    """Neutralise a cell a spreadsheet would treat as a formula (REQ-CSV-05)."""
    return "'" + text if text.startswith(_FORMULA_START) else text


def _num(value: float | None) -> str:
    """A number with a dot as decimal separator, to one decimal, without a trailing .0."""
    return "" if value is None else f"{round(value, 1):g}"


def factor_ids(profile: Profile) -> list[str]:
    """Factor IDs in catalogue order (F1.01, F1.02, ... F3.10)."""
    return sorted(profile.factors, key=lambda f: tuple(int(x) for x in f[1:].split(".")))


def header(profile: Profile, values: bool = False) -> list[str]:
    cols = [*_IDENTITY, "codec", "duration_s", "status"]
    for f in factor_ids(profile):
        col = f.replace(".", "_")
        cols.append(f"{col}_score")
        if values:
            cols.append(f"{col}_value")
    cols += [f"{o}_score" for o in OBJECTIVES]
    cols += ["final_score", "rating", "gates_applied", "coverage_pct", "flags", "message"]
    cols += ["profile", "analyzer_version", "analyzed_at"]
    return cols


def _flags(r: TrackReport) -> str:
    flags = []
    if r.measurements is not None:
        if not r.tags.formats:
            flags.append("no_tags")
        if r.tags.problems:
            flags.append("tag_problems")
    return ";".join(flags)


def row(r: TrackReport, profile: Profile, analyzed_at: str, values: bool = False) -> list[str]:
    m = r.measurements
    by_factor = {f.factor: f for f in r.factors}
    cells = [
        r.tags.album,
        r.tags.title,
        r.tags.artist,
        r.tags.track_number,
        str(r.path),
        "" if r.size is None else str(r.size),
        r.detection.format.value if r.detection else "",
        _num(m.duration_s) if m else "",
        r.score.status.value,
    ]
    for f in factor_ids(profile):
        res = by_factor.get(f)
        cells.append(_num(res.score) if res else "")
        if values:
            cells.append(res.detail if res and res.score is not None else "")
    cells += [_num(r.score.objectives.get(o)) for o in OBJECTIVES]
    cells += [
        _num(r.score.final_score),
        r.score.rating or "",
        ";".join(r.score.gates),
        _num(r.score.coverage_pct) if r.score.coverage_pct or m else "",
        _flags(r),
        r.message,
        profile.label,
        __version__,
        analyzed_at,
    ]
    return [safe(c) for c in cells]


def now_iso() -> str:
    """The current time in ISO 8601 with the local time zone offset (REQ-CSV-03)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_csv(
    out: TextIO,
    reports: Iterable[TrackReport],
    profile: Profile,
    *,
    values: bool = False,
    analyzed_at: str | None = None,
) -> None:
    """Write the header and one row per report to ``out``, which must have been opened with
    ``newline=""`` so the CRLF endings are written as they are."""
    stamp = analyzed_at or now_iso()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header(profile, values))
    for r in reports:
        writer.writerow(row(r, profile, stamp, values))
