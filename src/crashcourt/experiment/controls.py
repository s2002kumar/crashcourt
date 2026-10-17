"""Controls reports: the recorded result of an instrument check that is not a batch, read by the
gate before new evidence may run.

C5 (oracle sensitivity) runs labeled saver mutants on the real stack and judges every trial with
the frozen checker (specification section 13). Its driver (tests/mutants/c5.py) writes a controls
report; the pre-registration's C5 instrument check names that report's path as its evidence and
states the methodology in the exact words of `C5_REQUIREMENT`.

The first matrix's methodology (`C5_METHOD`) was revised before the pre-registration was final,
after the original measurement failed (research/evidence/t-11/c5-original.json, kept as it was):
M3 and M4 run on W1 approve, M5 on W5 sequential, each to be caught by its intended invariant in
at least 19 of 20 trials. M1 and M2 are NOT EXERCISED by the first matrix's workloads. They are
out of scope for its instrument validation, never run as part of it, and never counted or shown
as passing: a report names them as exclusions, with the reason, beside its results.

Before a new-evidence batch is authorized, the instrument check (crashcourt.experiment.aggregate)
reads the report as committed in the pre-registration commit and refuses unless:

- the pre-registration's C5 requirement is `C5_REQUIREMENT`, word for word;
- the report states that requirement and the specification's threshold, and holds exactly the
  applicable mutants, each run on its pre-registered shape against its intended invariant, and
  exactly the exclusions, each as not exercised with its reason;
- every applicable mutant was caught in at least 19 of 20 trials, recounted from the counts (the
  report's own pass flag must agree, but is never trusted on its own), and its coverage line says
  how many of the specified mutants that is;
- it was produced under the pre-registration's frozen checker (checker and FROZEN.md hashes);
- the mutants, the driver, and the scenario files it ran are the files the pre-registration
  commit holds;
- the manifest it ran with has every value the pre-registration's environment requires.

A missing, malformed, failed, or mismatched report is a reason, never a pass. So is a report in
the original format: it covered M1 and M2 as if they were measurable.

C9 (sequential reference) runs the same operation sequence as each trial of the tranche's
single-writer, fault-free cells on InMemorySaver in one process, and compares the final state with
the Postgres run's: the head checkpoint's channels as the frozen checker's own reader decodes them
from the trial's snapshot, with the trial's thread id normalized away (specification section 13).
Its driver (tests/controls/c9.py) writes a report the gate reads the same way: every trial of every
C9 cell equal, each batch the pre-registered scenario at the cell's N, under the pinned checker,
in the pre-registered environment, from committed sources.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from crashcourt.experiment import gitproof
from crashcourt.experiment.fingerprint import mismatched
from crashcourt.experiment.preregistration import CheckerPin, Preregistration
from crashcourt.manifest import Manifest

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Commit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Id = Annotated[str, StringConstraints(pattern=r"^\S+$")]
Text = Annotated[str, StringConstraints(pattern=r"^\S(?:[\s\S]*\S)?$")]
Count = Annotated[int, Field(ge=0, strict=True)]

#: At least 19 of 20 trials (specification section 13).
C5_CAUGHT = 19
C5_TRIALS = 20
#: The instrument checks whose evidence is a controls report the gate reads and judges.
REPORTED_CHECKS: tuple[str, ...] = ("C5", "C9")
C9_DRIVER = "tests/controls/c9.py"
#: How a mutant out of the first matrix's scope is recorded: never PASS, never a count.
NOT_EXERCISED = "NOT_EXERCISED"
W1_APPROVE = "scenarios/w1-approve.yaml"
W5_SEQUENTIAL = "scenarios/w5-sequential.yaml"


@dataclass(frozen=True)
class Applicable:
    """A mutant the first matrix's C5 runs: its shape, and the invariant that must catch it."""

    id: str
    target: str
    scenario: str


@dataclass(frozen=True)
class Excluded:
    """A specified mutant the first matrix's workloads never exercise, and why."""

    id: str
    #: The invariant it was declared with (issue #30), kept for the record.
    target: str
    reason: str


@dataclass(frozen=True)
class Method:
    applicable: tuple[Applicable, ...]
    excluded: tuple[Excluded, ...]

    @property
    def specified(self) -> int:
        return len(self.applicable) + len(self.excluded)

    def coverage(self) -> str:
        """The line every report and summary carries, so three is never read as five of five."""
        out = ", ".join(e.id for e in self.excluded)
        return (
            f"{len(self.applicable)} of the {self.specified} specified saver mutants are "
            f"applicable to the first matrix and measured; {out} are {NOT_EXERCISED} and are not "
            "counted as passing"
        )


C5_METHOD = Method(
    applicable=(
        Applicable("M3", "I5a", W1_APPROVE),
        Applicable("M4", "I1b", W1_APPROVE),
        Applicable("M5", "I6b", W5_SEQUENTIAL),
    ),
    excluded=(
        Excluded(
            "M1",
            "I7a",
            "not exercised by the first matrix's workloads: a dropped pending-write row is read "
            "back only by a run that takes an op over after a crash that follows the row's commit, "
            "and that schedule is K1b's new-evidence question, which no control may preview",
        ),
        Excluded(
            "M2",
            "I5a",
            "not exercised by the first matrix's workloads: none of them reads a checkpoint by id",
        ),
    ),
)

#: The pre-registration's C5 requirement, word for word: the gate refuses any other.
C5_REQUIREMENT = (
    "Saver mutants, as revised before finalization after the original measurement failed (M1 0 of "
    "20, M2 0 of 20, M5 on W1 INVALID by the I3 alarm; research/evidence/t-11/c5-original.json): "
    "M3 on W1 approve caught by I5a, M4 on W1 approve caught by I1b, and M5 on W5 sequential "
    "caught by I6b, each in at least 19 of 20 trials. M1 and M2 are NOT EXERCISED by the first "
    "matrix's workloads, out of scope for its instrument validation, and never counted or shown "
    "as passing: M1's dropped row is read back only after a crash that follows its commit, which "
    "is K1b's new-evidence question, and no first-matrix workload reads a checkpoint by id (M2)."
)


#: The pre-registration's C9 requirement, word for word: the gate refuses any other.
C9_REQUIREMENT = (
    "Sequential reference: for each single-writer, fault-free C1 cell of the tranche under sync "
    "durability (C1-w1-sync and C1-w5-sync in the first discovery tranche), every trial of one "
    "canonical batch at the cell's N reaches the same final state as the same operation sequence "
    "run on InMemorySaver in one process: the head checkpoint's channels as the frozen checker's "
    "reader decodes them from the trial's snapshot (W1: order, plan, resume, decision, executed; "
    "W5: messages as type, id, and content), with the trial's thread id normalized away."
)


def c9_cells(prereg: Preregistration) -> tuple[str, ...]:
    """The cells C9 compares: the tranche's C1 controls with one worker under sync durability."""
    return tuple(
        c.id
        for c in prereg.tranche_cells
        if c.control == "C1" and c.durability == "sync" and c.workers == 1
    )


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Threshold(_Model):
    caught: Count
    trials: Count


class CheckerUsed(_Model):
    version: Id
    checker_sha256: Sha256
    frozen_md_sha256: Sha256
    registry_sha256: Sha256


class MutantResult(_Model):
    """One applicable mutant's batch, judged trial by trial with the frozen checker."""

    id: Id
    defect: str
    #: The invariant that must catch it.
    target: Id
    #: The scenario file it ran, repository-relative.
    scenario: Text
    batch: Id
    scenario_sha256: Sha256
    manifest_sha256: Sha256
    trials: Count
    #: Trials the checker did not make INVALID.
    valid: Count
    #: Valid trials whose checker output shows an anomaly on the target invariant.
    caught: Count
    #: The target invariant's outcome in every trial, by value (INVALID and NOT_EXERCISED count).
    target_outcomes: dict[str, Count]
    #: Each INVALID trial's reason, by trial number.
    invalid: dict[str, str] = Field(default_factory=dict[str, str])
    #: Every other invariant that showed an anomaly, with how many trials it showed one in.
    other_anomalies: dict[str, Count] = Field(default_factory=dict[str, Count])


class Exclusion(_Model):
    """A specified mutant the report does not run: no first-matrix workload exercises it."""

    id: Id
    defect: str
    target: Id
    status: Literal["NOT_EXERCISED"]
    reason: Text


class ControlReport(_Model):
    #: 2: the revised methodology, with exclusions. Version 1 (the original measurement) is kept as
    #: a record and never read by the gate.
    v: Literal[2] = 2
    control: Literal["C5"]
    requirement: Text
    threshold: Threshold
    #: How many mutants the specification names, and how many of them this report measures.
    specified: Count
    applicable: Count
    coverage: Text
    checker: CheckerUsed
    #: The commit the batches ran from (provenance; the sources below are what is checked).
    run_commit: Commit
    #: Repository path -> sha256 of the file as run: the mutants, the driver, the scenarios.
    sources: dict[str, Sha256]
    #: The manifest the batches ran with, as doctor wrote it.
    manifest: dict[str, JsonValue]
    mutants: tuple[MutantResult, ...]
    excluded: tuple[Exclusion, ...]
    #: Whether every applicable mutant was caught at the threshold. Says nothing of the exclusions.
    applicable_passed: bool

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"


class ReferenceBatch(_Model):
    """One C9 cell's batch: each trial's Postgres final state against the in-process one."""

    cell: Id
    scenario: Text
    scenario_sha256: Sha256
    batch: Id
    manifest_sha256: Sha256
    #: Whether the batch ran through the gate (authorization.json): C9 counts only such batches.
    authorized: bool
    trials: Count
    #: Trials whose two final states were equal after normalization.
    equal: Count
    #: Each trial that differed, by number, with what differed.
    differing: dict[str, str] = Field(default_factory=dict[str, str])
    #: Each trial whose Postgres state could not be decoded, by number, with why.
    undecodable: dict[str, str] = Field(default_factory=dict[str, str])


class ReferenceReport(_Model):
    v: Literal[1] = 1
    control: Literal["C9"]
    requirement: Text
    checker: CheckerUsed
    run_commit: Commit
    #: Repository path -> sha256 of the file as run: the driver and the scenarios.
    sources: dict[str, Sha256]
    manifest: dict[str, JsonValue]
    batches: tuple[ReferenceBatch, ...]
    #: Whether every trial of every batch was equal; computed from the counts, never trusted alone.
    passed: bool

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"


def recount_reference(report: ReferenceReport, prereg: Preregistration) -> list[str]:
    """Why the report's batches do not show C9 passing for every C9 cell; empty when they do."""
    failures: list[str] = []
    cells = c9_cells(prereg)
    if not cells:
        return ["the pre-registration's tranche has no single-writer, fault-free C1 cell for C9"]
    found = {b.cell: b for b in report.batches}
    if sorted(found) != sorted(cells) or len(report.batches) != len(cells):
        failures.append(f"it holds batches for {sorted(found)}, C9 compares {sorted(cells)}")
    for cell_id in cells:
        b = found.get(cell_id)
        if b is None:
            continue
        try:
            cell = prereg.cell(cell_id)
        except KeyError:
            failures.append(f"{cell_id} is not pre-registered")
            continue
        if not b.authorized:
            failures.append(f"{cell_id}'s batch did not run through the gate")
        if cell.scenario != b.scenario or cell.scenario_sha256 != b.scenario_sha256:
            failures.append(
                f"{cell_id}'s batch ran {b.scenario} at {b.scenario_sha256}, not the cell's"
            )
        if b.trials != cell.n:
            failures.append(
                f"{cell_id}'s batch has {b.trials} trials, the cell is pre-registered at {cell.n}"
            )
        if b.equal != b.trials or b.differing or b.undecodable:
            failures.append(
                f"{cell_id}: {b.equal} of {b.trials} trials equal, {len(b.differing)} differing, "
                f"{len(b.undecodable)} undecodable; C9 needs every trial equal"
            )
    return failures


def judge_reference(
    report: ReferenceReport,
    pin: CheckerPin | None,
    required: Mapping[str, JsonValue],
    prereg: Preregistration,
) -> list[str]:
    """Every reason the report does not show C9 passing under this pre-registration."""
    reasons: list[str] = []
    if report.requirement != C9_REQUIREMENT:
        reasons.append("it states a requirement other than the pre-registration's C9")
    failures = recount_reference(report, prereg)
    reasons.extend(failures)
    if report.passed != (not failures):
        reasons.append(f"it says passed is {report.passed}, but its counts say otherwise")
    reasons.extend(_checker_problems(report.checker, pin))
    reasons.extend(_manifest_problems(report.manifest, required))
    return reasons


def _checker_problems(used: CheckerUsed, pin: CheckerPin | None) -> list[str]:
    if pin is None:
        return ["the pre-registration names no frozen checker to compare it with"]
    return [
        f"it ran under {name} {got}, the pre-registration pins {want}"
        for name, got, want in (
            ("checker version", used.version, pin.version),
            ("checker_sha256", used.checker_sha256, pin.checker_sha256),
            ("FROZEN.md sha256", used.frozen_md_sha256, pin.frozen_md_sha256),
            ("registry_sha256", used.registry_sha256, pin.registry_sha256),
        )
        if got != want
    ]


def _manifest_problems(
    raw: Mapping[str, JsonValue], required: Mapping[str, JsonValue]
) -> list[str]:
    try:
        manifest = Manifest.from_json(json.dumps(raw))
    except ValueError as exc:
        return [f"its manifest is not one doctor writes: {exc}"]
    return [
        f"it ran with {key} {got!r}; the pre-registration requires {want!r}"
        for key, (want, got) in mismatched(manifest, required).items()
    ]


def recount(report: ControlReport) -> list[str]:
    """Why the report's counts do not show its applicable mutants caught at the threshold; empty
    when every one of them was caught often enough."""
    failures: list[str] = []
    for m in report.mutants:
        if not m.caught <= m.valid <= m.trials:
            failures.append(f"{m.id}: inconsistent counts ({m.caught} caught, {m.valid} valid)")
        elif m.trials != C5_TRIALS or m.caught < C5_CAUGHT:
            failures.append(
                f"{m.id} was caught by {m.target} in {m.caught} of {m.trials} trials; C5 needs "
                f"at least {C5_CAUGHT} of {C5_TRIALS}"
            )
    return failures


def _method_problems(report: ControlReport, method: Method) -> list[str]:
    """Whether the report holds exactly the methodology's mutants, shapes, targets, exclusions."""
    problems: list[str] = []
    if report.requirement != C5_REQUIREMENT:
        problems.append("it states another requirement than the pre-registered C5 methodology")
    excluded_ids = {e.id for e in method.excluded}
    counted = [m.id for m in report.mutants]
    for m_id in sorted(excluded_ids & set(counted)):
        problems.append(f"it counts {m_id}, which the first matrix does not exercise")
    want = {a.id: a for a in method.applicable}
    if sorted(counted) != sorted(want) or len(set(counted)) != len(counted):
        problems.append(f"it measures {counted}, not exactly {sorted(want)}")
    for m in report.mutants:
        spec = want.get(m.id)
        if spec is None:
            continue
        if (m.target, m.scenario) != (spec.target, spec.scenario):
            problems.append(
                f"{m.id} ran on {m.scenario} against {m.target}; the methodology is "
                f"{spec.scenario} against {spec.target}"
            )
    recorded = {e.id: e for e in report.excluded}
    if sorted(recorded) != sorted(excluded_ids) or len(recorded) != len(report.excluded):
        problems.append(
            f"it records {sorted(recorded)} as not exercised, not exactly {sorted(excluded_ids)}"
        )
    for e in method.excluded:
        found = recorded.get(e.id)
        if found is not None and (found.target, found.reason) != (e.target, e.reason):
            problems.append(f"it records {e.id}'s exclusion other than the methodology does")
    if (report.specified, report.applicable) != (method.specified, len(method.applicable)):
        problems.append(
            f"it says {report.applicable} of {report.specified} mutants are applicable, not "
            f"{len(method.applicable)} of {method.specified}"
        )
    if report.coverage != method.coverage():
        problems.append("its coverage line is not the methodology's")
    return problems


def judge(
    report: ControlReport,
    pin: CheckerPin | None,
    required: Mapping[str, JsonValue],
    method: Method = C5_METHOD,
) -> list[str]:
    """Every reason the report does not show C5 passing under this checker and environment."""
    reasons: list[str] = []
    if report.threshold != Threshold(caught=C5_CAUGHT, trials=C5_TRIALS):
        reasons.append(
            f"its threshold is {report.threshold.caught} of {report.threshold.trials}, not "
            f"{C5_CAUGHT} of {C5_TRIALS}"
        )
    reasons.extend(_method_problems(report, method))
    failures = recount(report)
    reasons.extend(failures)
    if report.applicable_passed != (not failures):
        reasons.append(
            f"it says applicable_passed is {report.applicable_passed}, but its counts say otherwise"
        )
    reasons.extend(_checker_problems(report.checker, pin))
    reasons.extend(_manifest_problems(report.manifest, required))
    return reasons


def missing(prereg: Preregistration, prereg_commit: str, repo: Path) -> list[str]:
    """Why the reported instrument checks do not allow new evidence; empty when they do. Each
    check's evidence is a repository path, read as committed in the pre-registration commit."""
    reasons: list[str] = []
    checks = {c.id: c for c in prereg.instrument_checks}
    for check_id in REPORTED_CHECKS:
        where = f"instrument check {check_id}"
        check = checks.get(check_id)
        if check is None:
            reasons.append(f"the pre-registration has no {where}")
            continue
        requirement = C5_REQUIREMENT if check_id == "C5" else C9_REQUIREMENT
        if check.requirement != requirement:
            reasons.append(
                f"{where}'s requirement is not the {check_id} methodology the gate enforces"
            )
        if check.evidence is None:
            reasons.append(f"{where} records no controls report")
            continue
        raw = gitproof.blob(repo, prereg_commit, check.evidence)
        if raw is None:
            reasons.append(
                f"{where}: {check.evidence} is not a file in the pre-registration commit"
            )
            continue
        found: list[str]
        try:
            if check_id == "C5":
                report = ControlReport.model_validate_json(raw)
                found = judge(report, prereg.checker, prereg.environment)
                found.extend(_sources(report.sources, required_sources(), repo, prereg_commit))
            else:
                reference = ReferenceReport.model_validate_json(raw)
                found = judge_reference(reference, prereg.checker, prereg.environment, prereg)
                found.extend(
                    _sources(reference.sources, reference_sources(prereg), repo, prereg_commit)
                )
        except ValueError as exc:
            reasons.append(f"{where}: {check.evidence} is not a controls report: {exc}")
            continue
        reasons.extend(f"{where} ({check.evidence}): {r}" for r in found)
    return reasons


def required_sources(method: Method = C5_METHOD) -> tuple[str, ...]:
    """The files a C5 report must name: the mutants, the driver, and every applicable shape."""
    shapes = sorted({a.scenario for a in method.applicable})
    return ("tests/mutants/savers.py", "tests/mutants/c5.py", *shapes)


def reference_sources(prereg: Preregistration) -> tuple[str, ...]:
    """The files a C9 report must name: the driver and every C9 cell's scenario."""
    wanted = set(c9_cells(prereg))
    scenarios = sorted(c.scenario for c in prereg.cells if c.id in wanted)
    return (C9_DRIVER, *scenarios)


def _sources(
    sources: Mapping[str, str], required: tuple[str, ...], repo: Path, commit: str
) -> list[str]:
    reasons = [f"it names no hash for {path}" for path in required if path not in sources]
    for path, digest in sorted(sources.items()):
        committed = gitproof.blob_sha256(repo, commit, path)
        if committed != digest:
            held = "does not hold it" if committed is None else f"holds {committed}"
            reasons.append(f"it ran {path} at {digest}; the pre-registration commit {held}")
    return reasons
