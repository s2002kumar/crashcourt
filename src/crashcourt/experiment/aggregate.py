"""cells.json: each pre-registered cell's counts, intervals, and prediction check, from verified
batches only.

Every batch is re-proved first (crashcourt.experiment.verify); one that fails is refused, and so is
the whole aggregation, since a summary that silently left a batch out would misstate its cell.

Pooling. Batches of one cell are pooled only when they share the pre-registration commit, the
frozen checker, and the scenario hash, and their manifests differ in no material field that a
recorded compatibility rule does not cover (crashcourt.experiment.fingerprint). Otherwise each set
is reported as its own group and the cell says it was not pooled and why. Nothing is pooled by
default, and the report lists every rule it applied.

Counting, per group and invariant, over the group's trials:
- an INVALID trial (checker.json names a trigger, or every outcome is INVALID) counts toward the
  group's INVALID share, with its trigger, and nothing else; above the pre-registered share the
  group is excluded;
- in every other trial the invariant's value is its outcome in result.json, or NOT_EXERCISED when
  checker.json lists it so (a trial with no result.json has every invariant not exercised), or
  NOT_EVALUATED when the checker said nothing about it;
- n is the trials where it was exercised, k those whose outcome is an anomaly (EXPECTED, GAP,
  VIOLATION_CANDIDATE, VIOLATION), with the Wilson 95% interval and, at k = 0, the rule of three.

Per invariant, three separate readings:
- `unstable`: a deterministic cell whose valid trials do not all show one value (0 of N or N of N
  is the expected shape; anything between hides a nondeterminism to find before reporting);
- `escalate`: any trial is VIOLATION_CANDIDATE, which starts the escalation chain;
- `prediction`: `as-predicted` when every valid trial shows a predicted value, `differs` when a
  predicted cell shows anything else, `recorded` for a cell whose outcome was honestly unknown,
  `missing` when no valid trial exists.
Per predicted observation (crashcourt.experiment.observe), matched in every valid trial apart
from any outcome: how many trials showed it, each one that did not with what it showed instead,
`unstable` when a deterministic cell's trials split, and the same prediction reading.
A group's status is the worst of: excluded, escalate, unstable, differs, missing, recorded,
as-predicted, over its invariants and its observations. Nothing here decides an outcome: the
checker wrote them all.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from crashcourt.experiment import controls, gitproof
from crashcourt.experiment.fingerprint import (
    CompatibilityRule,
    fingerprint_sha256,
    unexplained_between,
)
from crashcourt.experiment.gate import (
    Authorization,
    CheckerView,
    InstrumentCheck,
    InstrumentEvidence,
    ScenarioView,
    authorize_batch,
    rules_at,
)
from crashcourt.experiment.observe import ANOMALY, mismatch
from crashcourt.experiment.preregistration import (
    EVERY_INVARIANT,
    Cell,
    Observation,
    Preregistration,
)
from crashcourt.experiment.verify import TrialFiles, VerifiedBatch, verify_batch
from crashcourt.stats import count_sentence, rule_of_three, wilson

NOT_EXERCISED = "NOT_EXERCISED"
NOT_EVALUATED = "NOT_EVALUATED"

Prediction = Literal["as-predicted", "differs", "recorded", "missing"]
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
    #: Valid trials by value: an outcome, NOT_EXERCISED, or NOT_EVALUATED.
    values: dict[str, int]
    exercised: int
    anomalies: int
    wilson_95: tuple[float, float] | None
    rule_of_three_95: float | None
    sentence: str | None
    predicted: tuple[str, ...] | None
    prediction: Prediction
    unstable: bool
    escalate: bool

    @property
    def status(self) -> Status:
        if self.escalate:
            return "escalate"
        if self.unstable:
            return "unstable"
        return self.prediction


class ObservationSummary(_Model):
    observation: str
    #: Valid trials, and how many showed the observation.
    trials: int
    matched: int
    #: Each valid trial that did not show it ("<batch> trial <n>"), with what it showed instead.
    mismatches: dict[str, str]
    prediction: Prediction
    unstable: bool

    @property
    def status(self) -> Status:
        return "unstable" if self.unstable else self.prediction


class GroupSummary(_Model):
    batches: tuple[str, ...]
    fingerprint_sha256: str
    trials: int
    invalid: int
    invalid_reasons: dict[str, int]
    excluded: bool
    invariants: tuple[InvariantSummary, ...]
    observations: tuple[ObservationSummary, ...] = ()
    status: Status


class CellSummary(_Model):
    cell: str
    phase: str
    evidence: str
    confidence: str
    n: int
    pooled: bool
    #: Why groups were kept apart: material fields that differ, each with the values seen.
    not_pooled_because: dict[str, list[str]]
    groups: tuple[GroupSummary, ...]


class CellsReport(_Model):
    v: Literal[1] = 1
    preregistration_commit: str
    checker_sha256: str
    #: Every compatibility rule the pooling applied.
    compatibility_rules: tuple[CompatibilityRule, ...]
    cells: tuple[CellSummary, ...]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"


class AggregationRefused(Exception):
    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


def invalid_reason(t: TrialFiles) -> str | None:
    """The INVALID trigger of a trial, or None when it is valid."""
    invalid = t.checker.get("invalid")
    if invalid:
        return str(invalid.get("name") or invalid.get("reason") or "invalid")
    if t.result is not None:
        outcomes = [r["outcome"] for r in t.result.get("results", [])]
        if outcomes and all(o == "INVALID" for o in outcomes):
            reasons = sorted({str(r["reason"]) for r in t.result["results"] if r.get("reason")})
            return reasons[0] if reasons else "invalid"
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


def summarize_invariant(
    cell: Cell, invariant: str, valid: Sequence[TrialFiles]
) -> InvariantSummary:
    values = Counter(_value(t, invariant) for t in valid)
    exercised = sum(c for v, c in values.items() if v not in (NOT_EXERCISED, NOT_EVALUATED))
    anomalies = sum(c for v, c in values.items() if v in ANOMALY)
    prediction = next((p for p in cell.predicted if p.invariant == invariant), None) or next(
        (p for p in cell.predicted if p.invariant == EVERY_INVARIANT), None
    )
    predicted = prediction.expect if prediction else None
    reading: Prediction
    if not valid:
        reading = "missing"
    elif cell.confidence == "unknown" or predicted is None:
        reading = "recorded"
    elif set(values) <= set(predicted):
        reading = "as-predicted"
    else:
        reading = "differs"
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
        prediction=reading,
        unstable=cell.deterministic and len(values) > 1,
        escalate="VIOLATION_CANDIDATE" in values,
    )


def summarize_observation(
    cell: Cell, observation: Observation, valid: Sequence[tuple[str, TrialFiles]]
) -> ObservationSummary:
    mismatches = {
        where: found for where, t in valid if (found := mismatch(observation, t)) is not None
    }
    matched = len(valid) - len(mismatches)
    reading: Prediction
    if not valid:
        reading = "missing"
    elif cell.confidence == "unknown":
        reading = "recorded"
    else:
        reading = "as-predicted" if not mismatches else "differs"
    return ObservationSummary(
        observation=observation.label(),
        trials=len(valid),
        matched=matched,
        mismatches=mismatches,
        prediction=reading,
        unstable=cell.deterministic and 0 < matched < len(valid),
    )


def summarize_group(
    cell: Cell, batches: Sequence[VerifiedBatch], max_invalid: float
) -> GroupSummary:
    trials = [(f"{b.batch.batch} trial {t.trial}", t) for b in batches for t in b.trials]
    reasons = Counter(r for r in (invalid_reason(t) for _, t in trials) if r is not None)
    invalid = sum(reasons.values())
    excluded = bool(trials) and invalid / len(trials) > max_invalid
    named = [(where, t) for where, t in trials if invalid_reason(t) is None]
    valid = [t for _, t in named]
    invariants = tuple(summarize_invariant(cell, name, valid) for name in _invariants(cell, valid))
    observations = tuple(summarize_observation(cell, o, named) for o in cell.observations)
    readings: list[Status] = [*(i.status for i in invariants), *(o.status for o in observations)]
    statuses: list[Status] = ["excluded"] if excluded else readings or ["missing"]
    return GroupSummary(
        batches=tuple(b.batch.batch for b in batches),
        fingerprint_sha256=fingerprint_sha256(batches[0].manifest, batches[0].host),
        trials=len(trials),
        invalid=invalid,
        invalid_reasons=dict(sorted(reasons.items())),
        excluded=excluded,
        invariants=invariants,
        observations=observations,
        status=min(statuses, key=SEVERITY.index),
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
            if _pool_key(g[0]) == _pool_key(b) and not unexplained_between(
                g[0].environment, b.environment, rules
            ):
                g.append(b)
                break
        else:
            groups.append([b])
    return groups


def _apart(
    groups: Sequence[Sequence[VerifiedBatch]], rules: Sequence[CompatibilityRule]
) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    first = groups[0][0]
    for g in groups[1:]:
        for key, (x, y) in unexplained_between(first.environment, g[0].environment, rules).items():
            found.setdefault(key, set()).update({repr(x), repr(y)})
        names = ("cell", "preregistration", "checker", "freeze", "scenario")
        for name, x, y in zip(names, _pool_key(first), _pool_key(g[0]), strict=True):
            if x != y:
                found.setdefault(name, set()).update({x, y})
    return {k: sorted(v) for k, v in sorted(found.items())}


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
        cells.append(
            CellSummary(
                cell=cell.id,
                phase=cell.phase,
                evidence=cell.evidence,
                confidence=cell.confidence,
                n=cell.n,
                pooled=len(groups) == 1,
                not_pooled_because=_apart(groups, rules),
                groups=tuple(summarize_group(cell, g, prereg.max_invalid_share) for g in groups),
            )
        )
    return CellsReport(
        preregistration_commit=commits.pop(),
        checker_sha256=checkers.pop(),
        compatibility_rules=tuple(rules),
        cells=tuple(cells),
    )


def instrument_ready(repo: Path, rules: Sequence[CompatibilityRule] = ()) -> InstrumentCheck:
    """The gate's instrument check: every instrument cell of the pre-registration has one pooled
    group, from batches under the same pre-registration commit, that came out as predicted, and
    every instrument cell ran in one environment, which new evidence must then match. Every
    instrument check whose evidence is a controls report (C5) must pass as committed in the
    pre-registration commit (crashcourt.experiment.controls)."""

    def check(
        prereg: Preregistration, prereg_commit: str, dirs: Sequence[Path]
    ) -> InstrumentEvidence:
        reported = controls.missing(prereg, prereg_commit, repo)
        if not dirs:
            ids = [c.id for c in prereg.cells if c.phase == "instrument"]
            cells = [f"instrument cell {i} has no batch" for i in ids]
            return InstrumentEvidence([*cells, *reported], [], None)
        try:
            report = aggregate(dirs, repo, rules)
        except AggregationRefused as exc:
            problems = [f"instrument batches: {p}" for p in exc.problems]
            return InstrumentEvidence([*problems, *reported], [], None)
        missing: list[str] = list(reported)
        if report.preregistration_commit != prereg_commit:
            missing.append("the instrument batches ran under another pre-registration")
        found = {c.cell: c for c in report.cells}
        for cell in prereg.cells:
            if cell.phase != "instrument":
                continue
            summary = found.get(cell.id)
            if summary is None:
                missing.append(f"instrument cell {cell.id} has no batch")
            elif not summary.pooled:
                missing.append(f"instrument cell {cell.id} ran in more than one environment")
            elif summary.groups[0].status != "as-predicted":
                missing.append(
                    f"instrument cell {cell.id} is {summary.groups[0].status}, not as predicted"
                )
        environments = [verify_batch(d, repo).environment for d in dirs]
        for other in environments[1:]:
            differing = unexplained_between(environments[0], other, rules)
            if differing:
                missing.append(
                    f"the instrument cells ran in different environments ({', '.join(differing)})"
                )
                break
        used = sorted(b for c in report.cells for g in c.groups for b in g.batches)
        return InstrumentEvidence(missing, used, environments[0])

    return check


def authorize(
    source: Path | None,
    trials: int,
    root: Path,
    instrument: Sequence[Path] = (),
    *,
    checker: CheckerView | None = None,
    scenarios: ScenarioView | None = None,
) -> Authorization:
    """`authorize_batch` as the runner and the CLI call it: for a new-evidence cell, the
    instrument batches given are checked under the compatibility rules recorded at HEAD."""
    rules = rules_at(root, gitproof.head(root))
    return authorize_batch(
        source,
        trials,
        root,
        checker=checker,
        scenarios=scenarios,
        instrument=instrument,
        instrument_check=instrument_ready(root, rules),
    )
