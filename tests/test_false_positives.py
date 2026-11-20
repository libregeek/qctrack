"""False-positive regression corpus (todo 7.6): genuine files must not be rated poor, and
real defects and transcodes must still be caught. Cases live in ``fp_cases.py``."""

from pathlib import Path

import pytest

from fp_cases import CASES, Case
from qctrack.core.analyze import analyze_file
from qctrack.core.profile import load_default

P = load_default()


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("fp_corpus")


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_case(case: Case, work: Path) -> None:
    path = work / case.name
    case.build(work, path)
    r = analyze_file(path, P)
    s = r.score
    got = (s.status.value, s.rating, s.gates, s.final_score)
    print(f"{case.name} ({case.finding}): {got}")
    assert s.status.value == case.expect.status, got
    assert s.gates == case.expect.gates, got
    if case.expect.ratings is None:
        assert s.rating is None and r.advice is None, got
    else:
        assert s.rating in case.expect.ratings, got
        assert r.advice is not None and r.advice.action.value == case.expect.action, (
            r.advice,
            got,
        )
