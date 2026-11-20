"""Build the false-positive regression corpus and show how each file scores.

Usage (from the project root): ``python3 tools/accuracy/fp_corpus.py OUT_DIR``

Writes every case of ``tests/fp_cases.py`` to OUT_DIR (intermediate files go to
OUT_DIR/work), scores it with the default profile and prints one line per file with its
expected outcome. Exit status 1 when any case misses its expectation. The files can then
be inspected by hand, for example with ``qctrack scan OUT_DIR -v``.
"""

import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from fp_cases import CASES  # noqa: E402
from qctrack.core.analyze import analyze_file  # noqa: E402
from qctrack.core.profile import load_default  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    profile = load_default()
    misses = 0
    for case in CASES:
        path = out / case.name
        case.build(work, path)
        report = analyze_file(path, profile)
        s, action = report.score, report.advice.action.value if report.advice else None
        e = case.expect
        ok = (
            s.status.value == e.status
            and s.gates == e.gates
            and (s.rating is None if e.ratings is None else s.rating in e.ratings)
            and action == e.action
        )
        misses += not ok
        want = "/".join(sorted(e.ratings)) if e.ratings else e.status
        score = "" if s.final_score is None else f"{s.final_score:5.1f}"
        print(
            f"{'ok  ' if ok else 'MISS'} {case.name:34s} {case.finding:8s} {score:>5} "
            f"{s.rating or s.status.value:11s} {','.join(s.gates):6s} {action or '':22s} "
            f"want {want}" + (f" {','.join(e.gates)}" if e.gates else "")
        )
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
