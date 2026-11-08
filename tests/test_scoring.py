from pathlib import Path

import pytest

from qctrack.core.authenticity import Verdict
from qctrack.core.profile import load_default, load_profile
from qctrack.core.scoring import (
    FactorResult,
    Status,
    objective_of,
    rating_for,
    score_piecewise,
    score_track,
    score_verdict,
    unsupported_score,
)

P = load_default()


def fr(factor: str, score: float | None, verdict: Verdict | None = None) -> FactorResult:
    return FactorResult(factor, score, verdict)


def full(score: float, **override: float) -> list[FactorResult]:
    """Every weighted factor of the default profile at one score, with overrides."""
    return [fr(f, override.get(f, score)) for f, w in P.factors.items() if w > 0]


# --- the worked examples in requirements section 6.4 ----------------------------------


def test_sample_row_1_final_score_and_rating() -> None:
    # Objective scores 94.0 / 98.5 / 95.2 combine to 95.9, rating A, no gates.
    s = score_track([fr("F1.01", 94.0), fr("F2.01", 98.5), fr("F3.01", 95.2)], P)
    assert s.objectives == {"O1": 94.0, "O2": 98.5, "O3": 95.2}
    assert (s.final_score, s.rating, s.gates, s.coverage_pct) == (95.9, "A", (), 100.0)
    assert s.status is Status.SCORED


def test_sample_row_2_gate_g4_listed_when_f3_02_fails() -> None:
    results = [fr("F1.01", 81.5), fr("F2.01", 96.0), fr("F3.02", 0.0, Verdict.FAIL)]
    s = score_track(results, P)
    assert s.gates == ("G4",) and s.rating == "D" and s.final_score is not None
    assert s.final_score <= 59


def test_sample_row_2_exact_numbers() -> None:
    s = score_track([fr("F1.01", 81.5), fr("F2.01", 96.0), fr("F3.01", 18.3)], P)
    assert (s.final_score, s.rating) == (57.4, "D")


def test_sample_row_3_unsupported() -> None:
    s = unsupported_score()
    assert (s.status, s.final_score, s.rating, s.gates, s.coverage_pct) == (
        Status.UNSUPPORTED,
        None,
        None,
        ("G1",),
        0.0,
    )


# --- gates ----------------------------------------------------------------------------


def test_g4_caps_a_high_score_at_59() -> None:
    s = score_track(full(100), P)
    assert s.final_score == 100.0 and s.rating == "A"
    bad = [fr(r.factor, 100.0, Verdict.FAIL if r.factor == "F3.02" else None) for r in full(100)]
    s = score_track(bad, P)
    assert s.final_score == 59 and s.rating == "D" and s.gates == ("G4",)


@pytest.mark.parametrize("factor", ["F2.01", "F2.02"])
def test_g2_caps_at_39(factor: str) -> None:
    results = [fr(r.factor, 100.0, Verdict.FAIL if r.factor == factor else None) for r in full(100)]
    s = score_track(results, P)
    assert (s.final_score, s.rating, s.gates) == (39.0, "F", ("G2",))


def test_g3_caps_flac_checksum_failure() -> None:
    s = score_track([fr("F2.04", 0.0, Verdict.FAIL), fr("F1.01", 100.0)], P)
    assert s.gates == ("G3",) and s.final_score is not None and s.final_score <= 39


def test_lowest_cap_wins_when_several_gates_apply() -> None:
    results = [
        fr("F2.01", 100.0, Verdict.FAIL),
        fr("F3.02", 100.0, Verdict.FAIL),
        fr("F1.01", 100.0),
    ]
    s = score_track(results, P)
    assert s.gates == ("G2", "G4") and s.final_score == 39.0


def test_warn_does_not_trigger_a_gate() -> None:
    s = score_track([fr("F2.01", 50.0, Verdict.WARN), fr("F3.02", 50.0, Verdict.WARN)], P)
    assert s.gates == ()


# --- coverage, renormalisation, weights ------------------------------------------------


def test_not_evaluated_factors_are_excluded_and_weights_renormalised() -> None:
    s = score_track([fr("F1.01", 100.0), fr("F1.06", 0.0), fr("F1.03", None)], P)
    # (30*100 + 20*0) / 50 = 60; F1.03 does not count.
    assert s.objectives["O1"] == 60.0 and s.objectives["O2"] is None


def test_objective_without_evaluated_factors_is_dropped_from_the_final() -> None:
    s = score_track([fr("F1.01", 80.0), fr("F2.01", None)], P)
    assert s.final_score == 80.0


def test_coverage_is_share_of_applicable_weight() -> None:
    # Applicable: F1.01 (25*30) and F3.01 (45*25); only F1.01 evaluated.
    s = score_track([fr("F1.01", 100.0), fr("F3.01", None)], P)
    assert s.coverage_pct == pytest.approx(100 * 750 / (750 + 1125), abs=0.05)


def test_g5_marks_low_coverage_provisional() -> None:
    s = score_track([fr("F1.01", 90.0), fr("F3.01", None)], P)
    assert s.status is Status.PROVISIONAL and "G5" in s.gates and s.rating == "A"


def test_full_coverage_is_scored() -> None:
    s = score_track(full(80), P)
    assert s.status is Status.SCORED and s.coverage_pct == 100.0 and s.gates == ()


def test_info_factors_never_change_the_score() -> None:
    a = score_track([fr("F1.01", 70.0)], P)
    b = score_track([fr("F1.01", 70.0), fr("F1.02", 0.0), fr("F2.09", 0.0)], P)
    assert a == b


def test_nothing_evaluated_is_unscored() -> None:
    s = score_track([fr("F1.01", None)], P)
    assert (s.status, s.final_score, s.rating) == (Status.UNSCORED, None, None)


def test_unknown_factor_and_bad_score_are_rejected() -> None:
    with pytest.raises(ValueError, match="not in the profile"):
        score_track([fr("F9.99", 50.0)], P)
    with pytest.raises(ValueError, match="0-100"):
        score_track([fr("F1.01", 101.0)], P)


def test_custom_weights_change_the_score(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text('[factors]\n"F1.01" = 90\n')
    heavy = load_profile(path)
    results = [fr("F1.01", 100.0), fr("F1.06", 0.0)]
    assert score_track(results, heavy).final_score == pytest.approx(81.8, abs=0.05)
    assert score_track(results, P).final_score == 60.0


# --- ratings and factor scoring helpers -------------------------------------------------


@pytest.mark.parametrize(
    ("score", "rating"),
    [(100, "A"), (90, "A"), (89.9, "B"), (75, "B"), (74.9, "C"), (60, "C"),
     (59.9, "D"), (40, "D"), (39.9, "F"), (0, "F")],
)  # fmt: skip
def test_rating_bands(score: float, rating: str) -> None:
    assert rating_for(score, P) == rating


def test_objective_of() -> None:
    assert [objective_of(f) for f in ("F1.01", "F2.14", "F3.10")] == ["O1", "O2", "O3"]


def test_categorical_scores() -> None:
    assert [score_verdict(v) for v in (Verdict.PASS, Verdict.WARN, Verdict.FAIL)] == [100, 50, 0]


@pytest.mark.parametrize(
    ("kbps", "score"),
    [(32, 0), (64, 0), (96, 25), (128, 50), (144, 62.5), (160, 75), (208, 87.5), (256, 100),
     (320, 100)],
)  # fmt: skip
def test_f1_01_mp3_bitrate_bands(kbps: float, score: float) -> None:
    assert score_piecewise(kbps, P.mp3_bitrate_anchors) == pytest.approx(score)


def test_piecewise_works_for_lower_is_better() -> None:
    anchors = [(0.0, 100.0), (1.0, 50.0), (5.0, 0.0)]
    assert score_piecewise(0.5, anchors) == 75 and score_piecewise(9, anchors) == 0
