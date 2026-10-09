"""Batch and result records, schema v1. Frozen with the events: a change needs schema v2.

Every record says where it came from (`provenance`: a harness run or a fixture) and carries the
sha256 of its batch's manifest, so a release build can refuse fixtures and any result that does
not belong to the batch it names. A fixture's batch id starts with DEMO-, and only a fixture's.
A batch also names the scenario it ran, by the content hash of the validated scenario model
(crashcourt.spec.content), so two batches of the same scenario can be told from two batches of
scenarios that merely look alike.

A result records one outcome per invariant. The five verdicts are PASS, VIOLATION, EXPECTED,
GAP, and INVALID. Two further outcomes are not verdicts:

- VIOLATION_CANDIDATE: a guaranteeing clause forbids the anomaly, and it waits for a
  de-harnessed repro and a human review. Only then may it become VIOLATION.
- NO_ANOMALY_UNSPECIFIED: no anomaly, and no clause covers the scenario. It is never PASS.

Outcomes are distinct values; compare them whole, never by prefix (VIOLATION_CANDIDATE starts
with VIOLATION).

What each outcome needs, enforced when a record is loaded:

| Outcome | clause | evidence | repro | review |
| --- | --- | --- | --- | --- |
| PASS | required | optional | none | none |
| EXPECTED | required | required | optional | none |
| GAP | none | required | optional | none |
| VIOLATION_CANDIDATE | required | required | optional | none |
| VIOLATION | required | required | required | required |
| NO_ANOMALY_UNSPECIFIED | none | optional | none | none |
| INVALID | optional | optional | none | none |

INVALID also needs a reason. EXPECTED, GAP, and both kinds of violation describe an anomaly, so
they cite the evidence for it.

The JSON Schemas carry these rules too (`if`/`then` under `allOf`), so a reader that validates
with JSON Schema alone, the evidence explorer for one, rejects what the models reject. Two rules
are left to the models: one outcome per invariant, and a result's manifest hash matching its
batch's. tests/data/records-v1.json holds cases both must agree on.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Annotated, Any, Literal, Self, get_args

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
FIXTURE_PREFIX = "DEMO-"

#: An identifier: no whitespace anywhere.
Id = Annotated[str, StringConstraints(pattern=r"^\S+$")]
#: Free text: not empty, and no leading or trailing whitespace.
Text = Annotated[str, StringConstraints(pattern=r"^\S(?:[\s\S]*\S)?$")]
Count = Annotated[int, Field(ge=0, strict=True)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

_ANOMALY = frozenset({"EXPECTED", "GAP", "VIOLATION_CANDIDATE", "VIOLATION"})
_NEEDS_CLAUSE = frozenset({"PASS", "EXPECTED", "VIOLATION_CANDIDATE", "VIOLATION"})
_NO_CLAUSE = frozenset({"GAP", "NO_ANOMALY_UNSPECIFIED"})


def _when(outcomes: frozenset[str], then: dict[str, Any]) -> dict[str, Any]:
    """A JSON Schema rule: for these outcomes, the object must also match `then`."""
    return {
        "if": {
            "type": "object",
            "properties": {"outcome": {"enum": sorted(outcomes)}},
            "required": ["outcome"],
        },
        "then": {"type": "object", **then},
    }


def _outcome_rules(schema: dict[str, Any]) -> None:
    """The outcome table, in JSON Schema. Absent and null are the same to the models."""
    string = {"type": "string"}
    null = {"type": "null"}
    no_anomaly = frozenset(OUTCOMES) - _ANOMALY
    schema["allOf"] = [
        _when(_NEEDS_CLAUSE, {"required": ["clause"], "properties": {"clause": string}}),
        _when(_NO_CLAUSE, {"properties": {"clause": null}}),
        _when(
            _ANOMALY,
            {
                "required": ["evidence"],
                "properties": {"evidence": {"type": "array", "minItems": 1}},
            },
        ),
        _when(
            frozenset({"VIOLATION"}),
            {
                "required": ["repro", "review"],
                "properties": {"repro": string, "review": {"type": "object"}},
            },
        ),
        _when(no_anomaly, {"properties": {"repro": null}}),
        _when(frozenset(OUTCOMES) - {"VIOLATION"}, {"properties": {"review": null}}),
        _when(frozenset({"INVALID"}), {"required": ["reason"], "properties": {"reason": string}}),
    ]


def _provenance_rule(schema: dict[str, Any]) -> None:
    """In JSON Schema: a fixture's batch id starts with DEMO-, and only a fixture's."""
    demo = {"type": "string", "pattern": f"^{FIXTURE_PREFIX}"}
    schema["if"] = {
        "type": "object",
        "properties": {"provenance": {"const": "fixture"}},
        "required": ["provenance"],
    }
    schema["then"] = {"type": "object", "properties": {"batch": demo}}
    schema["else"] = {"type": "object", "properties": {"batch": {"not": demo}}}


def _check_provenance(batch: str, provenance: Provenance) -> None:
    if (provenance == "fixture") != batch.startswith(FIXTURE_PREFIX):
        raise ValueError(
            f"batch {batch!r} with provenance {provenance}: a fixture's batch id starts with "
            f"{FIXTURE_PREFIX}, and only a fixture's"
        )


class BatchRecord(BaseModel):
    """batch.json: one batch, its provenance, and the manifest it ran under.

    A fixture's batch id starts with DEMO-, and only a fixture's.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_provenance_rule)

    v: Version
    batch: Id
    provenance: Provenance
    #: sha256 of the batch's manifest.json bytes.
    manifest_sha256: Sha256
    scenario_sha256: Sha256 = Field(
        description=(
            "Content hash of the validated scenario the batch ran: sha256 of the frozen scenario "
            "model's canonical JSON (crashcourt.spec.content), not of the YAML bytes. Equivalent "
            "YAML hashes the same; any change of meaning changes it. T-07's loader produces it; "
            "a fixture carries the hash of its demonstration scenario."
        )
    )

    @model_validator(mode="after")
    def _provenance_matches_batch(self) -> Self:
        _check_provenance(self.batch, self.provenance)
        return self


class Review(BaseModel):
    """The human review a VIOLATION requires."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer: Text
    reviewed: date


class InvariantResult(BaseModel):
    """One invariant's outcome in one trial.

    Each outcome needs some of clause, evidence, repro, review, and reason; the table is in
    crashcourt.spec.records, and the rules are under allOf here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_outcome_rules)

    invariant: Id
    outcome: Outcome
    #: The registered clause the outcome rests on, by id.
    clause: Id | None = None
    #: References to the evidence: artifact paths, with a JSON pointer where one applies.
    evidence: tuple[Text, ...] = ()
    #: The de-harnessed repro, by id or path.
    repro: Text | None = None
    review: Review | None = None
    #: Why the trial is INVALID, or a note on any other outcome.
    reason: Text | None = None

    @model_validator(mode="after")
    def _outcome_requirements(self) -> Self:
        outcome = self.outcome
        problems: list[str] = []
        if outcome in _NEEDS_CLAUSE and self.clause is None:
            problems.append("needs a clause")
        if outcome in _NO_CLAUSE and self.clause is not None:
            problems.append("has no clause (no clause covers the scenario)")
        if outcome in _ANOMALY and not self.evidence:
            problems.append("needs evidence of the anomaly")
        if outcome == "VIOLATION" and self.repro is None:
            problems.append("needs a de-harnessed repro")
        if outcome not in _ANOMALY and self.repro is not None:
            problems.append("has no repro (there is no anomaly to reproduce)")
        if outcome == "VIOLATION" and self.review is None:
            problems.append("needs a review")
        if outcome != "VIOLATION" and self.review is not None:
            problems.append("has no review (a reviewed candidate is a VIOLATION)")
        if outcome == "INVALID" and self.reason is None:
            problems.append("needs a reason")
        if problems:
            raise ValueError(f"{outcome} {'; '.join(problems)}")
        return self


class ResultRecord(BaseModel):
    """result.json: the outcomes of one trial, one per invariant.

    A fixture's batch id starts with DEMO-, and only a fixture's. Two rules are checked when
    records are loaded and are not in this JSON Schema: one outcome per invariant, and a
    manifest hash equal to the batch's.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=_provenance_rule)

    v: Version
    batch: Id
    cell: Id
    trial: Count
    provenance: Provenance
    #: sha256 of the manifest of the batch this result belongs to; must equal the batch's.
    manifest_sha256: Sha256
    results: tuple[InvariantResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_outcome_per_invariant(self) -> Self:
        _check_provenance(self.batch, self.provenance)
        counts = Counter(r.invariant for r in self.results)
        repeated = sorted(name for name, count in counts.items() if count > 1)
        if repeated:
            raise ValueError(f"one outcome per invariant; repeated: {', '.join(repeated)}")
        return self
