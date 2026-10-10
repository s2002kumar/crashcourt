"""cells.json: each pre-registered cell's counts, intervals, and prediction check, from verified
batches only.

Every batch is re-proved first (crashcourt.experiment.verify); one that fails is refused, and so is
the whole aggregation, since a summary that silently left a batch out would misstate its cell.

Pooling. Batches of one cell are pooled only when they share the pre-registration commit, the
frozen checker, and the scenario hash, and their manifests differ in no material field that a
recorded compatibility rule does not cover (crashcourt.experiment.fingerprint). Otherwise each set
is reported as its own group and the cell says it was not pooled and why. Nothing is pooled by
default.

Counting, per group and invariant, over the group's trials:
- an INVALID trial (checker.json names a trigger, or every outcome is INVALID) counts toward the
  group's INVALID share and nothing else; above the pre-registered share the group is excluded;
- in every other trial the invariant's value is its outcome in result.json, or NOT_EXERCISED when
  checker.json lists it so (a trial with no result.json has every invariant not exercised), or
  NOT_EVALUATED when the checker said nothing about it;
- n is the trials where it was exercised, k those whose outcome is an anomaly (EXPECTED, GAP,
  VIOLATION_CANDIDATE, VIOLATION), with the Wilson 95% interval and, at k = 0, the rule of three.

Comparison with the prediction, per invariant: `unstable` when a deterministic cell's trials do not
all agree; `escalate` when any trial is VIOLATION_CANDIDATE; `as-predicted` when every trial shows
one predicted value; `differs` when a predicted cell shows anything else; `recorded` for a cell
whose outcome was honestly unknown. Nothing here decides an outcome: the checker wrote them all.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from crashcourt.experiment.fingerprint import CompatibilityRule, fingerprint_sha256, unexplained
from crashcourt.experiment.gate import InstrumentCheck
from crashcourt.experiment.preregistration import EVERY_INVARIANT, Cell, Preregistration
from crashcourt.experiment.verify import TrialFiles, VerifiedBatch, verify_batch
from crashcourt.stats import count_sentence, rule_of_three, wilson

ANOMALY: frozenset[str] = frozenset({"EXPECTED", "GAP", "VIOLATION_CANDIDATE", "VIOLATION"})
NOT_EXERCISED = "NOT_EXERCISED"
NOT_EVALUATED = "NOT_EVALUATED"

Status = Literal[
    "as-predicted", "differs", "unstable", "escalate", "recorded", "missing", "excluded"
]
#: Worst first: a group's status is the worst of its invariants'.
SEVERITY: tuple[Status, ...] = (
    "excluded",
    "escalate",
    "unstable",
    "differs",
    "missing",
    "recorded",
    "as-predicted",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InvariantSummary(_Model):
    invariant: str
    #: Trials by value: an outcome, NOT_EXERCISED, or NOT_EVALUATED (INVALID trials excluded).
    values: dict[str, int]
    exercised: int
    anomalies: int
    wilson_95: tuple[float, float] | None
    rule_of_three_95: float | None
    sentence: str | None
    predicted: tuple[str, ...] | None
    status: Status


class GroupSummary(_Model):
    batches: tuple[str, ...]
    fingerprint_sha256: str
    trials: int
    invalid: int
    invalid_reasons: dict[str, int]
    excluded: bool
    invariants: tuple[InvariantSummary, ...]
    status: Status


class CellSummary(_Model):
    cell: str
    phase: str
    evidence: str
    confidence: str
    pooled: bool
    #: Why groups were kept apart: material fields that differ, each with the values seen.
    not_pooled_because: dict[str, list[str]]
    groups: tuple[GroupSummary, ...]


class CellsReport(_Model):
    v: Literal[1] = 1
    preregistration_commit: str
    checker_sha256: str
    cells: tuple[CellSummary, ...]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"


class AggregationRefused(Exception):
    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


def _is_invalid(t: TrialFiles) -> str | None:
    """The INVALID reason of a trial, or None when it is valid."""
    invalid = t.checker.get("invalid")
    if invalid:
        return str(invalid.get("name") or invalid.get("reason") or "invalid")
    if t.result is not None:
        outcomes = [r["outcome"] for r in t.result.get("results", [])]
        if outcomes and all(o == "INVALID" for o in outcomes):
            reasons = {r.get("reason") for r in t.result["results"]}
            return str(sorted(r for r in reasons if r)[0]) if any(reasons) else "invalid"
    return None


def _value(t: TrialFiles, invariant: str) -> str:
    if t.result is not None:
        for r in t.result.get("results", []):
            if r["invariant"] == invariant:
                return str(r["outcome"])
    listed = {str(n.get("invariant")) for n in t.checker.get("not_exercised", [])}
    if invariant in listed or t.result is None:
        return NOT_EXERCISED
    return NOT_EVALUATED


def _invariants(cell: Cell, trials: Iterable[TrialFiles]) -> list[str]:
    names = [p.invariant for p in cell.predicted if p.invariant != EVERY_INVARIANT]
    for t in trials:
        for r in (t.result or {}).get("results", []):
            if r["invariant"] not in names and r["outcome"] != "INVALID":
                names.append(str(r["invariant"]))
    return names


def _summarize_invariant(
    cell: Cell, invariant: str, valid: Sequence[TrialFiles]
) -> InvariantSummary:
    values = Counter(_value(t, invariant) for t in valid)
    exercised = sum(c for v, c in values.items() if v not in (NOT_EXERCISED, NOT_EVALUATED))
    anomalies = sum(c for v, c in values.items() if v in ANOMALY)
    prediction = next((p for p in cell.predicted if p.invariant == invariant), None) or next(
        (p for p in cell.predicted if p.invariant == EVERY_INVARIANT), None
    )
    predicted = prediction.expect if prediction else None
    status: Status
    if not valid:
        status = "missing"
    elif "VIOLATION_CANDIDATE" in values:
        status = "escalate"
    elif cell.deterministic and len(values) > 1:
        status = "unstable"
    elif cell.confidence == "unknown" or predicted is None:
        status = "recorded"
    elif set(values) <= set(predicted):
        status = "as-predicted"
    else:
        status = "differs"
    interval = wilson(anomalies, exercised) if exercised else None
    return InvariantSummary(
        invariant=invariant,
        values=dict(sorted(values.items())),
        exercised=exercised,
        anomalies=anomalies,
        wilson_95=(round(interval.low, 4), round(interval.high, 4)) if interval else None,
        rule_of_three_95=round(rule_of_three(exercised), 4)
        if exercised and not anomalies
        else None,
        sentence=count_sentence(anomalies, exercised, f"an anomaly on {invariant}")
        if exercised
        else None,
        predicted=predicted,
        status=status,
    )


def _summarize_group(
    cell: Cell, batches: Sequence[VerifiedBatch], max_invalid: float
) -> GroupSummary:
    trials = [t for b in batches for t in b.trials]
    reasons = Counter(r for r in (_is_invalid(t) for t in trials) if r is not None)
    invalid = sum(reasons.values())
    excluded = bool(trials) and invalid / len(trials) > max_invalid
    valid = [t for t in trials if _is_invalid(t) is None]
    invariants = tuple(_summarize_invariant(cell, name, valid) for name in _invariants(cell, valid))
    statuses: list[Status] = (
        ["excluded"] if excluded else [i.status for i in invariants] or ["missing"]
    )
    worst = min(statuses, key=SEVERITY.index)
    return GroupSummary(
        batches=tuple(b.batch.batch for b in batches),
        fingerprint_sha256=fingerprint_sha256(batches[0].manifest),
        trials=len(trials),
        invalid=invalid,
        invalid_reasons=dict(sorted(reasons.items())),
        excluded=excluded,
        invariants=invariants,
        status=worst,
    )


def _pool_key(b: VerifiedBatch) -> tuple[str, ...]:
    a = b.authorization
    return (
        a.cell,
        a.preregistration_commit,
        a.checker_sha256,
        a.frozen_md_sha256,
        a.scenario_sha256,
    )


def group(
    batches: Sequence[VerifiedBatch], rules: Sequence[CompatibilityRule] = ()
) -> list[list[VerifiedBatch]]:
    """Batches of one cell in poolable sets: same key, no unexplained material difference."""
    groups: list[list[VerifiedBatch]] = []
    for b in batches:
        for g in groups:
            if _pool_key(g[0]) == _pool_key(b) and not unexplained(
                g[0].manifest, b.manifest, rules
            ):
                g.append(b)
                break
        else:
            groups.append([b])
    return groups


def aggregate(
    batch_dirs: Sequence[Path],
    repo: Path,
    rules: Sequence[CompatibilityRule] = (),
) -> CellsReport:
    """cells.json for these batches, every one verified; AggregationRefused otherwise."""
    verified: list[VerifiedBatch] = []
    problems: list[str] = []
    for d in batch_dirs:
        try:
            verified.append(verify_batch(d, repo))
        except Exception as exc:  # BatchNotVerified or unreadable records  # noqa: BLE001
            problems.append(str(exc))
    if problems:
        raise AggregationRefused(problems)
    if not verified:
        raise AggregationRefused(["no batches"])
    commits = {b.authorization.preregistration_commit for b in verified}
    checkers = {b.authorization.checker_sha256 for b in verified}
    if len(commits) > 1 or len(checkers) > 1:
        raise AggregationRefused(
            [f"batches span pre-registrations {sorted(commits)} and checkers {sorted(checkers)}"]
        )
    prereg = verified[0].prereg
    cells: list[CellSummary] = []
    for cell in prereg.cells:
        mine = [b for b in verified if b.authorization.cell == cell.id]
        if not mine:
            continue
        groups = group(mine, rules)
        apart: dict[str, set[str]] = {}
        for g in groups[1:]:
            for key, (x, y) in unexplained(groups[0][0].manifest, g[0].manifest, rules).items():
                apart.setdefault(key, set()).update({repr(x), repr(y)})
            for name, x, y in zip(
                ("cell", "preregistration", "checker", "freeze", "scenario"),
                _pool_key(groups[0][0]),
                _pool_key(g[0]),
                strict=True,
            ):
                if x != y:
                    apart.setdefault(name, set()).update({x, y})
        cells.append(
            CellSummary(
                cell=cell.id,
                phase=cell.phase,
                evidence=cell.evidence,
                confidence=cell.confidence,
                pooled=len(groups) == 1,
                not_pooled_because={k: sorted(v) for k, v in sorted(apart.items())},
                groups=tuple(_summarize_group(cell, g, prereg.max_invalid_share) for g in groups),
            )
        )
    return CellsReport(
        preregistration_commit=commits.pop(),
        checker_sha256=checkers.pop(),
        cells=tuple(cells),
    )


def instrument_ready(repo: Path, rules: Sequence[CompatibilityRule] = ()) -> InstrumentCheck:
    """The gate's instrument check: every instrument cell of the pre-registration has one pooled
    group, from batches under the same pre-registration commit, that came out as predicted."""

    def check(
        prereg: Preregistration, prereg_commit: str, dirs: Sequence[Path]
    ) -> tuple[list[str], list[str]]:
        try:
            report = aggregate(dirs, repo, rules) if dirs else None
        except AggregationRefused as exc:
            return [f"instrument batches: {p}" for p in exc.problems], []
        missing: list[str] = []
        if report is not None and report.preregistration_commit != prereg_commit:
            missing.append("the instrument batches ran under another pre-registration")
        found = {c.cell: c for c in (report.cells if report else ())}
        for cell in prereg.cells:
            if cell.phase != "instrument":
                continue
            summary = found.get(cell.id)
            if summary is None:
                missing.append(f"instrument cell {cell.id} has no batch")
            elif not summary.pooled or summary.groups[0].status != "as-predicted":
                status = summary.groups[0].status if summary.pooled else "not pooled"
                missing.append(f"instrument cell {cell.id} is {status}, not as predicted")
        used = [b for c in (report.cells if report else ()) for g in c.groups for b in g.batches]
        return missing, sorted(used)

    return check
