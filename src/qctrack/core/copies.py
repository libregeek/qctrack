"""Ranking copies of the same track (requirements 4.5, 5.10) for ``compare`` and
``--duplicates``.

Copies are compared pair by pair with the steps of section 5.10, in order; the first step
that separates them by more than its tie threshold decides. Like scoring, this is a pure
function of stored measurements and advice, so ``rescore --duplicates`` works without audio.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from qctrack.core.advice import Action, BestCopy
from qctrack.core.analyze import TrackReport
from qctrack.core.profile import Profile

#: Cutoffs above this are not compared: nothing audible or codec-related lies above it,
#: and Nyquist alone would make a 48 kHz file beat an equal 44.1 kHz one.
CUTOFF_CEILING_HZ = 20000.0
#: F1.01 scores at least this far apart (one bitrate band) decide the encode step when the
#: cutoffs tie.
BITRATE_BAND_POINTS = 25.0
#: REQ-CMP-03: differences that point to different masters rather than different encodes.
MASTER_DURATION_S = 2.0
MASTER_LOUDNESS_LU = 2.0
MASTER_PLR_DB = 2.0
#: REQ-CMP-04: a duration difference beyond this suggests different recordings.
DIFFERENT_TRACK_S = 10.0


@dataclass(frozen=True)
class Decision:
    """How two copies compare: ``sign`` > 0 when the first is better, 0 when equivalent."""

    sign: int
    step: str
    reason: str


@dataclass(frozen=True)
class CopyGroup:
    number: int
    #: Best first.
    members: tuple[TrackReport, ...]
    #: Between the first two members.
    decision: Decision
    different_masters: bool
    warnings: tuple[str, ...] = ()
    #: How many copies from the top are equivalent to the best one (1 when it wins).
    tied: int = 1

    @property
    def summary(self) -> str:
        """One line: which copy to keep and why, or which copies are equally good."""
        if self.tied >= 2:
            if self.tied == len(self.members) == 2:
                return "Equivalent, keep either"
            names = [r.path.name for r in self.members[: self.tied]]
            return "Equivalent, keep any of " + ", ".join(names[:-1]) + " or " + names[-1]
        return f"keep {self.members[0].path.name}: {self.decision.step}: {self.decision.reason}"


def _cutoff(r: TrackReport) -> float | None:
    m = r.measurements
    if m is None:
        return None
    measured = [s for s in m.spectrum if s.measured]
    if not measured:
        return None
    cut = min(s.cutoff_hz if s.has_cliff else s.nyquist_hz for s in measured)
    return min(cut, CUTOFF_CEILING_HZ)


def _factor_score(r: TrackReport, factor: str) -> float | None:
    return next((f.score for f in r.factors if f.factor == factor), None)


def compare_pair(a: TrackReport, b: TrackReport, profile: Profile) -> Decision:
    """Section 5.10 for two scored copies."""
    aa, ab = a.advice, b.advice
    assert aa is not None and ab is not None
    damaged = (aa.action is Action.REPLACE_DAMAGED, ab.action is Action.REPLACE_DAMAGED)
    if damaged[0] != damaged[1]:
        return Decision(-1 if damaged[0] else 1, "integrity", "the other copy is damaged")
    lossy = (aa.best_copy is BestCopy.LOSSY_SOURCE, ab.best_copy is BestCopy.LOSSY_SOURCE)
    if lossy[0] != lossy[1]:
        return Decision(
            -1 if lossy[0] else 1, "authenticity", "the other copy comes from a lossy source"
        )
    # Step 3 (lossless before lossy) applies once FLAC is supported (phase 2).
    ca, cb = _cutoff(a), _cutoff(b)
    if ca is not None and cb is not None:
        if abs(ca - cb) >= float(profile.advice["equivalent_cutoff_hz"]):
            hi, lo = max(ca, cb), min(ca, cb)
            return Decision(
                1 if ca > cb else -1,
                "encode quality",
                f"content to {hi / 1000:.1f} kHz against {lo / 1000:.1f} kHz",
            )
    ka, kb = _factor_score(a, "F1.01"), _factor_score(b, "F1.01")
    if ka is not None and kb is not None and abs(ka - kb) >= BITRATE_BAND_POINTS:
        return Decision(1 if ka > kb else -1, "encode quality", "a higher bitrate band")
    oa, ob = a.score.objectives.get("O4"), b.score.objectives.get("O4")
    if profile.advice["rank_by_mastering"] and oa is not None and ob is not None:
        if abs(oa - ob) >= float(profile.advice["equivalent_mastering"]):
            return Decision(
                1 if oa > ob else -1,
                "mastering",
                f"mastering {max(oa, ob):.0f} against {min(oa, ob):.0f} (a matter of taste)",
            )
    return Decision(0, "equivalent", "no step separates them")


def _loudness(r: TrackReport) -> tuple[float, float] | None:
    m = r.measurements
    if m is None or not m.loudness:
        return None
    best = max(m.loudness, key=lambda x: x.frames)
    return best.integrated_lufs, best.plr_db


def _duration(r: TrackReport) -> float | None:
    return r.measurements.duration_s if r.measurements is not None else None


def different_masters(copies: Sequence[TrackReport]) -> bool:
    """REQ-CMP-03: durations, loudness or peak-to-loudness ratio too far apart."""
    for i, a in enumerate(copies):
        for b in copies[i + 1 :]:
            da, db = _duration(a), _duration(b)
            if da is not None and db is not None and abs(da - db) > MASTER_DURATION_S:
                return True
            la, lb = _loudness(a), _loudness(b)
            if la is not None and lb is not None:
                if abs(la[0] - lb[0]) > MASTER_LOUDNESS_LU or abs(la[1] - lb[1]) > MASTER_PLR_DB:
                    return True
    return False


def normalise(text: str) -> str:
    """Tag text for matching: Unicode NFKC, case folded, whitespace collapsed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def same_track_warnings(copies: Sequence[TrackReport]) -> tuple[str, ...]:
    """REQ-CMP-04: signs that the files may not be the same recording."""
    out: list[str] = []
    durations = [d for r in copies if (d := _duration(r)) is not None]
    if durations and max(durations) - min(durations) > DIFFERENT_TRACK_S:
        out.append(
            f"durations differ by {max(durations) - min(durations):.0f} s: "
            "these may be different recordings"
        )
    for field in ("artist", "title"):
        values = {normalise(getattr(r.tags, field)) for r in copies}
        values.discard("")
        if len(values) > 1:
            out.append(f"{field} tags differ: these may be different tracks")
    return tuple(out)


def rank(copies: Sequence[TrackReport], profile: Profile, number: int = 1) -> CopyGroup:
    """Rank two or more scored copies of one track, best first."""
    scored = [r for r in copies if r.advice is not None]

    def cmp(a: TrackReport, b: TrackReport) -> int:
        return -compare_pair(a, b, profile).sign

    order = sorted(scored, key=functools.cmp_to_key(cmp))
    decision = (
        compare_pair(order[0], order[1], profile)
        if len(order) >= 2
        else Decision(0, "equivalent", "fewer than two copies could be scored")
    )
    tied = 1
    while tied < len(order) and compare_pair(order[0], order[tied], profile).sign == 0:
        tied += 1
    if len(order) < 2:
        tied = len(order)
    return CopyGroup(
        number,
        tuple(order),
        decision,
        different_masters(order),
        same_track_warnings(order),
        tied,
    )


def find_duplicates(reports: Iterable[TrackReport], profile: Profile) -> list[CopyGroup]:
    """REQ-DUP-01: group scored files with matching artist and title tags whose durations
    lie within ``advice.duplicate_duration_s`` of each other; rank each group."""
    window = float(profile.advice["duplicate_duration_s"])
    by_tags: dict[tuple[str, str], list[TrackReport]] = defaultdict(list)
    for r in reports:
        artist, title = normalise(r.tags.artist), normalise(r.tags.title)
        if r.advice is not None and artist and title and _duration(r) is not None:
            by_tags[(artist, title)].append(r)
    groups: list[CopyGroup] = []
    for key in sorted(by_tags):
        members = sorted(by_tags[key], key=lambda r: (_duration(r) or 0.0, str(r.path)))
        cluster: list[TrackReport] = []
        clusters: list[list[TrackReport]] = []
        for r in members:
            if cluster and (_duration(r) or 0.0) - (_duration(cluster[0]) or 0.0) > window:
                clusters.append(cluster)
                cluster = []
            cluster.append(r)
        clusters.append(cluster)
        for c in clusters:
            if len(c) >= 2:
                groups.append(rank(c, profile, len(groups) + 1))
    return groups


def apply_groups(groups: Iterable[CopyGroup]) -> None:
    """Record each copy's group and rank on its report, and flag different masters."""
    for g in groups:
        for i, r in enumerate(g.members, start=1):
            r.copy_group, r.copy_rank = g.number, i
            r.copy_note = g.summary if i == 1 else ""
            if g.different_masters and r.advice is not None:
                if "different_masters" not in r.advice.flags:
                    r.advice = replace(r.advice, flags=(*r.advice.flags, "different_masters"))
