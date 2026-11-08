"""Self-contained HTML report (requirements 6.1).

One file: styles and script are inline, nothing is fetched, and a Content-Security-Policy meta
tag forbids it (REQ-HTM-01). All text that comes from tags and file names is escaped: the table
is rendered by Jinja2 with autoescape on, and the per-track detail is JSON in a data block,
with ``<``, ``>`` and ``&`` escaped, that the script turns into elements with ``textContent``,
never ``innerHTML`` (REQ-HTM-07). The table works without script; sorting, filtering and the
expandable details need it.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, TextIO

import jinja2

from qctrack import __version__
from qctrack.core.analyze import TrackReport
from qctrack.core.catalogue import FACTORS, OBJECTIVE_NAMES
from qctrack.core.profile import OBJECTIVES, Profile
from qctrack.core.scoring import Status, gate_legend

_TEMPLATE = "report.html.j2"
_BINS = 10


@dataclass(frozen=True)
class Row:
    """One line of the track table, already reduced to display text."""

    index: int
    album: str
    title: str
    artist: str
    path: str
    codec: str
    score: str
    score_sort: str
    rating: str
    rating_class: str
    gates: str


@dataclass(frozen=True)
class Bar:
    label: str
    count: int
    x: float
    y: float
    height: float


def _num(value: float | None) -> str:
    return "" if value is None else f"{round(value, 1):g}"


def _row(i: int, r: TrackReport) -> Row:
    s = r.score
    if s.status is Status.ERROR:
        rating, cls = "Error", "error"
    elif s.status is Status.UNSUPPORTED:
        rating, cls = "Unsupported", "unsupported"
    elif s.rating is None or s.final_score is None:
        rating, cls = "Unscored", "unsupported"
    else:
        rating = s.rating + ("*" if s.status is Status.PROVISIONAL else "")
        cls = s.rating
    return Row(
        i,
        r.tags.album,
        r.tags.title,
        r.tags.artist,
        str(r.path),
        r.detection.format.value if r.detection else "",
        _num(s.final_score) or "-",
        _num(s.final_score),
        rating,
        cls,
        ";".join(s.gates),
    )


def _json_for_script(data: object) -> str:
    """JSON that is safe inside a ``<script type="application/json">`` element."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def _detail(r: TrackReport) -> dict[str, Any]:
    s = r.score
    m = r.measurements
    d: dict[str, Any] = {
        "st": s.status.value,
        "cov": _num(s.coverage_pct),
        "o": [_num(s.objectives.get(o)) for o in OBJECTIVES],
        "f": [
            [f.factor, _num(f.score), f.verdict.value if f.verdict else "", f.detail]
            for f in r.factors
        ],
    }
    if r.message:
        d["msg"] = r.message
    if m is not None and m.duration_s is not None:
        d["dur"] = _num(m.duration_s)
    if r.size is not None:
        d["size"] = r.size
    if r.tags.problems:
        d["tags"] = r.tags.problems
    return d


def _histogram(reports: Sequence[TrackReport]) -> tuple[list[Bar], int]:
    counts = [0] * _BINS
    for r in reports:
        v = r.score.final_score
        if v is not None:
            counts[min(int(v // (100 / _BINS)), _BINS - 1)] += 1
    top = max(counts) or 1
    plot_h, slot, base = 90.0, 48.0, 110.0
    bars = []
    for i, c in enumerate(counts):
        h = plot_h * c / top
        label = f"{i * 10}–{i * 10 + 10}"
        bars.append(Bar(label, c, 10 + i * slot, base - h, h))
    return bars, sum(counts)


def _environment() -> jinja2.Environment:
    text = resources.files("qctrack.data").joinpath(_TEMPLATE).read_text("utf-8")
    return jinja2.Environment(
        loader=jinja2.DictLoader({_TEMPLATE: text}),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def write_html(
    out: TextIO,
    reports: Sequence[TrackReport],
    profile: Profile,
    *,
    analyzed_at: str,
) -> None:
    """Write the whole report to ``out``."""
    rows = [_row(i, r) for i, r in enumerate(reports)]
    ratings: Counter[str] = Counter(r.score.rating for r in reports if r.score.rating)
    status: Counter[Status] = Counter(r.score.status for r in reports)
    used_gates = sorted({g for r in reports for g in r.score.gates})
    legend = gate_legend(profile)
    bars, scored = _histogram(reports)
    weights = {f: w for f, w in profile.factors.items()}
    data = {
        "objectives": [[o, OBJECTIVE_NAMES[o], profile.objectives[o]] for o in OBJECTIVES],
        "factors": {f: [*FACTORS[f], weights.get(f, 0)] for f in FACTORS},
        "tracks": [_detail(r) for r in reports],
    }
    html = (
        _environment()
        .get_template(_TEMPLATE)
        .render(
            rows=rows,
            total=len(reports),
            ratings=[(k, ratings[k]) for k in "ABCDF"],
            provisional=status[Status.PROVISIONAL],
            unsupported=status[Status.UNSUPPORTED],
            errors=status[Status.ERROR],
            unscored=status[Status.UNSCORED],
            gates=[(g, legend[g]) for g in used_gates],
            bars=bars,
            scored=scored,
            profile=profile,
            analyzed_at=analyzed_at.replace("T", " "),
            generator=f"qctrack {__version__}",
            data_json=_json_for_script(data),
        )
    )
    out.write(html)
