"""Controls reports: the recorded result of an instrument check that is not a batch, read by the
gate before new evidence may run.

C5 (oracle sensitivity) runs labeled saver mutants M1-M5 on the real stack and judges every trial
with the frozen checker: each mutant must be caught by its target invariant in at least 19 of 20
trials (specification section 13). Its driver (tests/mutants/c5.py) writes a controls report;
the pre-registration's C5 instrument check names that report's path as its evidence.

Before a new-evidence batch is authorized, the instrument check (crashcourt.experiment.aggregate)
reads the report as committed in the pre-registration commit and refuses unless:

- it is a C5 report at the specification's threshold, covering exactly M1-M5, each with 20 trials;
- every mutant was caught in at least 19 of them, recounted from the per-mutant counts (the
  report's own `passed` must agree, but is never trusted on its own);
- it was produced under the pre-registration's frozen checker (checker and FROZEN.md hashes);
- the mutants and the driver it ran are the files the pre-registration commit holds;
- the manifest it ran with has every value the pre-registration's environment requires.

A missing, malformed, failed, or mismatched report is a reason, never a pass.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
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
Count = Annotated[int, Field(ge=0, strict=True)]

#: The saver mutants C5 covers (specification section 13).
C5_MUTANTS: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5")
#: At least 19 of 20 trials (specification section 13; the pre-registration's C5 requirement).
C5_CAUGHT = 19
C5_TRIALS = 20
#: The instrument checks whose evidence is a controls report the gate reads and judges.
REPORTED_CHECKS: tuple[str, ...] = ("C5",)


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
    """One mutant's batch, judged trial by trial with the frozen checker."""

    id: Id
    defect: str
    #: The invariant that must catch it.
    target: Id
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


class ControlReport(_Model):
    v: Literal[1] = 1
    control: Literal["C5"]
    requirement: str
    threshold: Threshold
    checker: CheckerUsed
    #: The commit the batches ran from (provenance; the sources below are what is checked).
    run_commit: Commit
    #: Repository path -> sha256 of the file as run: the mutants and the driver.
    sources: dict[str, Sha256]
    #: The manifest the batches ran with, as doctor wrote it.
    manifest: dict[str, JsonValue]
    mutants: tuple[MutantResult, ...]
    passed: bool

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"


def recount(report: ControlReport) -> list[str]:
    """Why the report's counts do not show a pass at the specification's threshold; empty when
    every mutant was caught often enough."""
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


def judge(
    report: ControlReport, pin: CheckerPin | None, required: Mapping[str, JsonValue]
) -> list[str]:
    """Every reason the report does not show C5 passing under this checker and environment."""
    reasons: list[str] = []
    if report.threshold != Threshold(caught=C5_CAUGHT, trials=C5_TRIALS):
        reasons.append(
            f"its threshold is {report.threshold.caught} of {report.threshold.trials}, not "
            f"{C5_CAUGHT} of {C5_TRIALS}"
        )
    ids = [m.id for m in report.mutants]
    if sorted(ids) != list(C5_MUTANTS) or len(set(ids)) != len(ids):
        reasons.append(f"it covers {ids}, not exactly {list(C5_MUTANTS)}")
    failures = recount(report)
    reasons.extend(failures)
    if report.passed != (not failures):
        reasons.append(f"it says passed is {report.passed}, but its counts say otherwise")
    if pin is None:
        reasons.append("the pre-registration names no frozen checker to compare it with")
    else:
        for name, got, want in (
            ("checker version", report.checker.version, pin.version),
            ("checker_sha256", report.checker.checker_sha256, pin.checker_sha256),
            ("FROZEN.md sha256", report.checker.frozen_md_sha256, pin.frozen_md_sha256),
            ("registry_sha256", report.checker.registry_sha256, pin.registry_sha256),
        ):
            if got != want:
                reasons.append(f"it ran under {name} {got}, the pre-registration pins {want}")
    try:
        manifest = Manifest.from_json(json.dumps(report.manifest))
    except ValueError as exc:
        reasons.append(f"its manifest is not one doctor writes: {exc}")
    else:
        for key, (want, got) in mismatched(manifest, required).items():
            reasons.append(f"it ran with {key} {got!r}; the pre-registration requires {want!r}")
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
        if check.evidence is None:
            reasons.append(f"{where} records no controls report")
            continue
        raw = gitproof.blob(repo, prereg_commit, check.evidence)
        if raw is None:
            reasons.append(
                f"{where}: {check.evidence} is not a file in the pre-registration commit"
            )
            continue
        try:
            report = ControlReport.model_validate_json(raw)
        except ValueError as exc:
            reasons.append(f"{where}: {check.evidence} is not a controls report: {exc}")
            continue
        if report.control != check_id:
            reasons.append(f"{where}: {check.evidence} reports {report.control}")
            continue
        found = judge(report, prereg.checker, prereg.environment)
        found.extend(_sources(report.sources, repo, prereg_commit))
        reasons.extend(f"{where} ({check.evidence}): {r}" for r in found)
    return reasons


def _sources(sources: dict[str, str], repo: Path, commit: str) -> list[str]:
    reasons: list[str] = []
    if not sources:
        reasons.append("it names no source files")
    for path, digest in sorted(sources.items()):
        committed = gitproof.blob_sha256(repo, commit, path)
        if committed != digest:
            held = "does not hold it" if committed is None else f"holds {committed}"
            reasons.append(f"it ran {path} at {digest}; the pre-registration commit {held}")
    return reasons
