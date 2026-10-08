"""Batch and result records, schema v1. Frozen with the events: a change needs schema v2.

Every record says where it came from (`provenance`: a harness run or a fixture) and carries the
sha256 of its batch's manifest, so a release build can refuse fixtures and any result that does
not belong to the batch it names.

A result records one outcome per invariant. The five verdicts are PASS, VIOLATION, EXPECTED,
GAP, and INVALID. Two further outcomes are not verdicts and can never read as one:

- VIOLATION_CANDIDATE: a guaranteeing clause forbids the anomaly, and it waits for a
  de-harnessed repro and a human review. Only then may it become VIOLATION.
- NO_ANOMALY_UNSPECIFIED: no anomaly, and no clause covers the scenario. It is never PASS.

The validator enforces what each outcome needs: a VIOLATION cites a clause, its evidence, a
repro, and a review; a candidate cites a clause and evidence and has no review yet; PASS and
EXPECTED cite a clause; GAP and NO_ANOMALY_UNSPECIFIED cite none; INVALID gives a reason.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from crashcourt.spec.events import Version

SCHEMA_VERSION: Literal[1] = 1

Provenance = Literal["run", "fixture"]
Verdict = Literal["PASS", "VIOLATION", "EXPECTED", "GAP", "INVALID"]
Outcome = Literal[
    "PASS",
    "VIOLATION",
    "EXPECTED",
    "GAP",
    "INVALID",
    "VIOLATION_CANDIDATE",
    "NO_ANOMALY_UNSPECIFIED",
]
VERDICTS: tuple[str, ...] = get_args(Verdict)
OUTCOMES: tuple[str, ...] = get_args(Outcome)

Name = Annotated[str, StringConstraints(min_length=1)]
Count = Annotated[int, Field(ge=0, strict=True)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class BatchRecord(BaseModel):
    """batch.json: one batch, its provenance, and the manifest it ran under."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Version
    batch: Name
    provenance: Provenance
    #: sha256 of the batch's manifest.json bytes.
    manifest_sha256: Sha256


class Review(BaseModel):
    """The human review a VIOLATION requires."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer: Name
    reviewed: IsoDate


class InvariantResult(BaseModel):
    """One invariant's outcome in one trial."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    invariant: Name
    outcome: Outcome
    #: The registered clause the outcome rests on, by id.
    clause: Name | None = None
    #: References to the evidence: artifact paths, with a JSON pointer where one applies.
    evidence: tuple[Name, ...] = ()
    #: The de-harnessed repro, by id or path.
    repro: Name | None = None
    review: Review | None = None
    #: Why the trial is INVALID, or a note on any other outcome.
    reason: str | None = None

    @model_validator(mode="after")
    def _outcome_requirements(self) -> InvariantResult:
        problems: list[str] = []
        needs_clause = {"PASS", "EXPECTED", "VIOLATION", "VIOLATION_CANDIDATE"}
        if self.outcome in needs_clause and self.clause is None:
            problems.append("a clause")
        if self.outcome in {"GAP", "NO_ANOMALY_UNSPECIFIED"} and self.clause is not None:
            problems.append("no clause (no clause covers the scenario)")
        if self.outcome in {"VIOLATION", "VIOLATION_CANDIDATE"} and not self.evidence:
            problems.append("evidence")
        if self.outcome == "VIOLATION":
            if self.repro is None:
                problems.append("a de-harnessed repro")
            if self.review is None:
                problems.append("a review")
        if self.outcome == "VIOLATION_CANDIDATE" and self.review is not None:
            problems.append("no review yet (a reviewed candidate is a verdict)")
        if self.outcome == "INVALID" and not self.reason:
            problems.append("a reason")
        if problems:
            raise ValueError(f"{self.outcome} requires {', '.join(problems)}")
        return self


class ResultRecord(BaseModel):
    """result.json: the outcomes of one trial."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Version
    batch: Name
    cell: Name
    trial: Count
    provenance: Provenance
    #: sha256 of the manifest of the batch this result belongs to; must equal the batch's.
    manifest_sha256: Sha256
    results: tuple[InvariantResult, ...] = Field(min_length=1)
