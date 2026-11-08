"""JSON report (REQ-CLI-06, REQ-PRF-06) and its reader, the input of ``rescore``.

One file holds, for every track, all measurements plus the factor scores, objective scores,
final score, rating and gates, and for the whole scan the profile used (name, version, content
hash and values). ``rescore`` ignores the scores and recomputes them from the measurements.

The file is strict JSON: the few non-finite measurements (a silent track has -inf dB) are
written as the strings ``"-Infinity"``, ``"Infinity"`` and ``"NaN"``, which the reader accepts
back as floats. The averaged spectrum is rounded to 0.1 dB to keep the file small.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from qctrack import __version__
from qctrack.core.analyze import Measured, TrackReport
from qctrack.core.codec import measured_from_data, measured_to_data, profile_to_data
from qctrack.core.profile import Profile
from qctrack.core.scoring import FactorResult

FORMAT = "qctrack-report"
SCHEMA = 1
_SPECTRUM_DECIMALS = 1


class ReportFileError(Exception):
    """The file is not a report this version can read."""


def _plain(value: Any) -> Any:
    """Make floats strict-JSON safe, recursively."""
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _factor(f: FactorResult) -> dict[str, Any]:
    return {
        "factor": f.factor,
        "score": f.score,
        "verdict": f.verdict.value if f.verdict else None,
        "detail": f.detail,
    }


def track_data(r: TrackReport) -> dict[str, Any]:
    s = r.score
    return {
        "path": str(r.path),
        "status": s.status.value,
        "final_score": s.final_score,
        "rating": s.rating,
        "gates": list(s.gates),
        "coverage_pct": s.coverage_pct,
        "objectives": dict(s.objectives),
        "factors": [_factor(f) for f in r.factors],
        "message": r.message,
        "measured": measured_to_data(r.to_measured(), _SPECTRUM_DECIMALS),
    }


def write_json(
    out: TextIO,
    reports: Iterable[TrackReport],
    profile: Profile,
    *,
    analyzed_at: str,
    quick: bool = False,
) -> None:
    """Write the report to ``out``, one track at a time so memory stays flat."""
    head = {
        "format": FORMAT,
        "schema": SCHEMA,
        "generator": f"qctrack {__version__}",
        "analyzed_at": analyzed_at,
        "quick": quick,
        "profile": {
            "name": profile.name,
            "version": profile.version,
            "label": profile.label,
            "sha256": profile.content_hash,
            "values": profile_to_data(profile),
        },
    }
    out.write(json.dumps(_plain(head), indent=1, ensure_ascii=False, allow_nan=False)[:-2])
    out.write(',\n "tracks": [')
    counts: Counter[str] = Counter()
    for i, r in enumerate(reports):
        counts[r.score.status.value] += 1
        text = json.dumps(_plain(track_data(r)), ensure_ascii=False, allow_nan=False)
        out.write(("," if i else "") + "\n  " + text)
    out.write("\n ],\n")
    out.write(' "summary": ' + json.dumps({"tracks": sum(counts.values()), **counts}) + "\n}\n")


@dataclass
class ReportFile:
    analyzed_at: str
    quick: bool
    profile_label: str
    profile_sha256: str
    #: Each track's measurements, ready to be scored again.
    tracks: list[Measured]


def read_json(path: Path) -> ReportFile:
    """Read a report written by :func:`write_json`. Raises :class:`ReportFileError`."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as e:
        raise ReportFileError(f"{path}: {e.strerror or e}") from e
    except ValueError as e:
        raise ReportFileError(f"{path}: not valid JSON ({e})") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ReportFileError(f"{path}: not a qctrack report")
    if doc.get("schema") != SCHEMA:
        raise ReportFileError(
            f"{path}: report schema {doc.get('schema')!r} is not supported "
            f"(this version reads schema {SCHEMA})"
        )
    try:
        tracks = [measured_from_data(t["measured"], Path(t["path"])) for t in doc["tracks"]]
        profile = doc["profile"]
        return ReportFile(
            str(doc["analyzed_at"]),
            bool(doc.get("quick", False)),
            str(profile["label"]),
            str(profile["sha256"]),
            tracks,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ReportFileError(f"{path}: damaged report ({e})") from e
