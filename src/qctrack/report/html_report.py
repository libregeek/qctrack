"""Self-contained HTML report (requirements 6.1).

One file: styles and script are inline, nothing is fetched, and a Content-Security-Policy meta
tag forbids it (REQ-HTM-01). All text that comes from tags and file names is escaped: the table
is rendered by Jinja2 with autoescape on, and the per-track detail is JSON in a data block,
with ``<``, ``>`` and ``&`` escaped, that the script turns into elements with ``textContent``,
never ``innerHTML`` (REQ-HTM-07). The table works without script; sorting, filtering and the
expandable details need it.
"""

from __future__ import annotations

import base64
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, TextIO

import jinja2

from qctrack import __version__
from qctrack.core.advice import Action
from qctrack.core.analyze import TrackReport
from qctrack.core.catalogue import FACTORS, OBJECTIVE_NAMES
from qctrack.core.profile import OBJECTIVES, Profile
from qctrack.core.scoring import Status, gate_legend
from qctrack.core.spectrogram import row_fraction

_TEMPLATE = "report.html.j2"
_BINS = 10
_TICKS_HZ = (100, 1000, 5000, 10000, 16000, 20000)


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
    best_copy: str
    action: str
    #: Action priority (1 most urgent) for sorting; empty when there is no action.
    action_sort: str
    #: CSS class of the action badge.
    action_class: str
    gates: str


@dataclass(frozen=True)
class Bar:
    label: str
    count: int
    x: float
    y: float
    height: float


#: Badge colour per action: greens for keep, amber for optional replacements, reds for the
#: ones that matter, neutral when the tool cannot tell (same palette as the ratings).
ACTION_CLASS = {
    Action.KEEP: "a-keep",
    Action.KEEP_SOURCE_LIMITED: "a-keep-limited",
    Action.UNDETERMINED: "a-undetermined",
    Action.CONSIDER_MASTER: "a-optional",
    Action.REPLACE_LOW_BITRATE: "a-optional",
    Action.REPLACE_SOURCE: "a-replace",
    Action.REPLACE_DAMAGED: "a-damaged",
}


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
        r.advice.best_copy.value if r.advice else "",
        r.advice.action.value if r.advice else "",
        str(r.advice.action.priority) if r.advice else "",
        ACTION_CLASS[r.advice.action] if r.advice else "",
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


def _spectrogram(r: TrackReport) -> dict[str, Any] | None:
    """The picture with what the script needs to label it: the PNG as a data URI, and the
    heights (0-1 from the bottom) of the measured cutoff and of the axis ticks."""
    m = r.measurements
    img = m.spectrogram if m is not None else None
    if m is None or img is None or not img.png:
        return None
    out: dict[str, Any] = {
        "src": "data:image/png;base64," + base64.b64encode(img.png).decode(),
        "ticks": [
            [
                f"{hz // 1000} kHz" if hz >= 1000 else f"{hz} Hz",
                round(row_fraction(hz, img.rate), 4),
            ]
            for hz in _TICKS_HZ
            if hz < img.rate / 2
        ],
        "dur": _num(img.duration_s),
    }
    cut = next((s for s in m.spectrum if s.rate == img.rate and s.measured), None)
    if cut is not None and cut.has_cliff:
        out["cut"] = [round(cut.cutoff_hz), round(row_fraction(cut.cutoff_hz, img.rate), 4)]
    return out


def _detail(r: TrackReport, spectrograms: bool = False) -> dict[str, Any]:
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
    if r.advice is not None:
        a = r.advice
        d["adv"] = [a.best_copy.value, a.action.value, a.reason, list(a.flags)]
    if spectrograms and (sg := _spectrogram(r)) is not None:
        d["sg"] = sg
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
    spectrograms: bool = False,
) -> None:
    """Write the whole report to ``out``. Pictures are embedded only with ``spectrograms``."""
    rows = [_row(i, r) for i, r in enumerate(reports)]
    ratings: Counter[str] = Counter(r.score.rating for r in reports if r.score.rating)
    actions: Counter[Action] = Counter(r.advice.action for r in reports if r.advice)
    status: Counter[Status] = Counter(r.score.status for r in reports)
    used_gates = sorted({g for r in reports for g in r.score.gates})
    legend = gate_legend(profile)
    bars, scored = _histogram(reports)
    groups: dict[int, list[TrackReport]] = {}
    for r in reports:
        if r.copy_group is not None:
            groups.setdefault(r.copy_group, []).append(r)
    copy_groups = [
        (
            n,
            next((r.copy_note for r in members if r.copy_rank == 1), ""),
            "different_masters" in (members[0].advice.flags if members[0].advice else ()),
            [
                (r.copy_rank, str(r.path), _num(r.score.final_score) or "-", r.score.rating or "",
                 r.advice.action.value if r.advice else "",
                 ACTION_CLASS[r.advice.action] if r.advice else "")
                for r in sorted(members, key=lambda r: r.copy_rank or 0)
            ],
        )
        for n, members in sorted(groups.items())
    ]  # fmt: skip
    weights = {f: w for f, w in profile.factors.items()}
    data = {
        "objectives": [[o, OBJECTIVE_NAMES[o], profile.objectives[o]] for o in OBJECTIVES],
        "factors": {f: [*FACTORS[f], weights.get(f, 0)] for f in FACTORS},
        "tracks": [_detail(r, spectrograms) for r in reports],
    }
    html = (
        _environment()
        .get_template(_TEMPLATE)
        .render(
            rows=rows,
            total=len(reports),
            ratings=[(k, ratings[k]) for k in "ABCDF"],
            actions=[(a.value, actions[a], ACTION_CLASS[a]) for a in Action if actions[a]],
            provisional=status[Status.PROVISIONAL],
            unsupported=status[Status.UNSUPPORTED],
            errors=status[Status.ERROR],
            unscored=status[Status.UNSCORED],
            gates=[(g, legend[g]) for g in used_gates],
            copy_groups=copy_groups,
            bars=bars,
            scored=scored,
            profile=profile,
            analyzed_at=analyzed_at.replace("T", " "),
            generator=f"qctrack {__version__}",
            data_json=_json_for_script(data),
        )
    )
    out.write(html)
