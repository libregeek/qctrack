"""Pass 3 arithmetic: factor scores into objective scores, final score, gates, rating and
coverage (requirements section 5).

Everything here is a pure function of factor results and a ``Profile`` (REQ-ANL-05), so
``rescore`` can call it on saved measurements without audio. Turning a measurement into a
factor score (thresholds per factor) is done elsewhere; this module only combines them.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from qctrack.core.authenticity import Verdict
from qctrack.core.profile import OBJECTIVES, RATING_KEYS, Profile

_VERDICT_SCORE = {Verdict.PASS: 100.0, Verdict.WARN: 50.0, Verdict.FAIL: 0.0}


def verdict_for_score(score: float) -> Verdict:
    """Numeric factors: 100 is Pass, the Warn/Fail boundary (50) is still Warn."""
    return Verdict.PASS if score >= 100 else Verdict.WARN if score >= 50 else Verdict.FAIL


def score_verdict(verdict: Verdict) -> float:
    """Categorical factors: Pass 100, Warn 50, Fail 0 (REQ-SCR-02)."""
    return _VERDICT_SCORE[verdict]


def score_piecewise(value: float, anchors: Sequence[tuple[float, float]]) -> float:
    """Interpolate ``value`` between anchor points ``(value, score)`` (REQ-SCR-01, -05).

    Outside the anchors the score of the nearest end point applies. Anchors may rise or
    fall with the value, so it serves "higher is better" and "lower is better" factors.
    """
    pts = sorted(anchors)
    if value <= pts[0][0]:
        return pts[0][1]
    if value >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass(frozen=True)
class FactorResult:
    """One factor's outcome for a track.

    ``score`` is None when the factor applies but was not evaluated (for example in a quick
    scan). A factor that does not apply to the codec is simply left out of the input.
    """

    factor: str
    score: float | None
    verdict: Verdict | None = None
    #: The measured value in words, for reports (REQ-FAC-01).
    detail: str = ""


class Status(enum.Enum):
    SCORED = "Scored"
    PROVISIONAL = "Provisional"  # graded, but coverage is below the G5 minimum
    UNSUPPORTED = "Unsupported"  # gate G1
    UNSCORED = "Unscored"  # supported, but no weighted factor was evaluated


@dataclass(frozen=True)
class TrackScore:
    status: Status
    #: Objective scores, 0-100, rounded to one decimal; None where nothing was evaluated.
    objectives: dict[str, float | None]
    #: Final score after gates, one decimal; None when not scored.
    final_score: float | None
    rating: str | None
    #: Gate IDs whose condition held, in order (G1..G5).
    gates: tuple[str, ...]
    #: Share of applicable weight that was evaluated, percent.
    coverage_pct: float


def objective_of(factor: str) -> str:
    """``F2.06`` belongs to objective ``O2``."""
    return f"O{factor[1]}"


def rating_for(score: float, profile: Profile) -> str:
    for key in RATING_KEYS:
        if score >= profile.ratings[key]:
            return key
    return "F"


def unsupported_score() -> TrackScore:
    """Gate G1: the real format is not supported in this release; nothing is scored."""
    return TrackScore(Status.UNSUPPORTED, {o: None for o in OBJECTIVES}, None, None, ("G1",), 0.0)


def score_track(results: Iterable[FactorResult], profile: Profile) -> TrackScore:
    """Combine factor results into a ``TrackScore`` under ``profile``."""
    by_factor: dict[str, FactorResult] = {}
    for r in results:
        if r.factor not in profile.factors:
            raise ValueError(f"factor {r.factor!r} is not in the profile")
        if r.score is not None and not 0 <= r.score <= 100:
            raise ValueError(f"score for {r.factor} must be 0-100, got {r.score}")
        by_factor[r.factor] = r

    sums = {o: 0.0 for o in OBJECTIVES}  # sum of w_i * s_i
    weights = {o: 0.0 for o in OBJECTIVES}  # sum of w_i over evaluated factors
    applicable_weight = 0.0
    evaluated_weight = 0.0
    for r in by_factor.values():
        obj = objective_of(r.factor)
        w = profile.factors[r.factor]
        share = profile.objectives[obj] * w
        applicable_weight += share
        if r.score is not None:
            evaluated_weight += share
            sums[obj] += w * r.score
            weights[obj] += w
    coverage = 100.0 * evaluated_weight / applicable_weight if applicable_weight > 0 else 0.0

    raw: dict[str, float | None] = {
        o: sums[o] / weights[o] if weights[o] > 0 else None for o in OBJECTIVES
    }
    objectives = {o: None if v is None else round(v, 1) for o, v in raw.items()}
    scored = [(profile.objectives[o], v) for o, v in raw.items() if v is not None]
    total_weight = sum(w for w, _ in scored)
    gates = _gates_from_verdicts(by_factor)
    if total_weight <= 0:
        return TrackScore(Status.UNSCORED, objectives, None, None, tuple(gates), coverage)

    final = sum(w * v for w, v in scored) / total_weight
    for gate in gates:
        final = min(final, profile.gates[f"{gate}_cap"])
    final = round(final, 1)
    provisional = coverage < profile.gates["G5_min_coverage"]
    if provisional:
        gates.append("G5")
    return TrackScore(
        Status.PROVISIONAL if provisional else Status.SCORED,
        objectives,
        final,
        rating_for(final, profile),
        tuple(gates),
        round(coverage, 1),
    )


def _gates_from_verdicts(by_factor: dict[str, FactorResult]) -> list[str]:
    def failed(*factors: str) -> bool:
        return any(f in by_factor and by_factor[f].verdict is Verdict.FAIL for f in factors)

    gates: list[str] = []
    if failed("F2.01", "F2.02"):
        gates.append("G2")
    if failed("F2.04"):
        gates.append("G3")
    if failed("F3.02"):
        gates.append("G4")
    return gates
