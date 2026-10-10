"""research/preregistration.md: what a batch will run and what it is predicted to show, fixed in git
before it runs.

The file is prose for people and one fenced JSON block for the gate, the same arrangement as the
checker's FROZEN.md. The JSON block is the binding part: the frozen checker it runs under, the
environment it must run in, the seed rule, and every cell with its scenario hash, its number of
trials, its phase, and its predicted outcome per invariant. The commit that last changed this file
is the pre-registration commit; it must be an ancestor of every batch it covers, and it must come
after the checker's freeze commit (crashcourt.experiment.gate).

A draft names no frozen checker and leaves scenario hashes unset; the gate refuses to authorize any
batch from a draft. Finalizing fills them in, sets `state` to `final`, and is the founder's commit.

Phases. `instrument` cells (controls and published replications) run first; `new-evidence` cells
are refused until every instrument cell of the same pre-registration, checker, and environment has
come out as predicted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from crashcourt.spec.records import OUTCOMES

PREREGISTRATION_PATH = PurePosixPath("research/preregistration.md")
_FENCE = "```json"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Id = Annotated[str, StringConstraints(pattern=r"^\S+$")]
Text = Annotated[str, StringConstraints(pattern=r"^\S(?:[\s\S]*\S)?$")]

#: What a prediction may name: an outcome the checker writes, or that the invariant is not exercised
#: (omitted from result.json and listed in checker.json). VIOLATION is never predicted: the checker
#: never writes it.
PREDICTABLE: tuple[str, ...] = (
    *(o for o in OUTCOMES if o != "VIOLATION"),
    "NOT_EXERCISED",
)
Expected = Annotated[str, StringConstraints(pattern=r"^\S+$")]

Phase = Literal["instrument", "new-evidence"]
Evidence = Literal["control", "replication", "new-evidence"]
#: The published results a replication cell may reproduce (the T-10 pre-freeze list).
PUBLISHED_REPLICATIONS: frozenset[str] = frozenset(
    {"langgraph#8039", "langgraph#8796", "langgraph#8764"}
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


#: The invariant name of a prediction that covers every invariant the cell does not name.
EVERY_INVARIANT = "*"


class Prediction(_Model):
    """One invariant's predicted outcome in a cell. One value is a firm prediction; several mean
    the cell is predicted to show one of them in every trial. The invariant `*` covers every
    invariant the cell's other predictions do not name (a fault-free control predicts no anomaly
    anywhere)."""

    invariant: Id
    expect: tuple[Expected, ...] = Field(min_length=1)
    note: Text | None = None

    @model_validator(mode="after")
    def _known_outcomes(self) -> Self:
        unknown = [e for e in self.expect if e not in PREDICTABLE]
        if unknown:
            raise ValueError(f"{self.invariant}: cannot predict {unknown}; one of {PREDICTABLE}")
        return self


class Cell(_Model):
    id: Id
    phase: Phase
    evidence: Evidence
    #: For a replication: the published result it must reproduce.
    replication_of: str | None = None
    #: For a control: which one (C1, C2, ...).
    control: str | None = None
    #: The hypothesis from the specification the cell tests, when there is one.
    hypothesis: str | None = None
    workload: Id
    durability: Literal["sync", "async", "exit"]
    fault: Id | None = None
    workers: int = Field(ge=1, le=2)
    #: The trigger in words; the scenario file is the definition.
    trigger: Text
    #: The scenario file, relative to the repository root.
    scenario: Text
    #: The content hash of that scenario; null only in a draft.
    scenario_sha256: Sha256 | None = None
    #: The batch's master seed, as the scenario file states it; null only in a draft.
    master_seed: Id | None = None
    #: Trials per batch of this cell: fixed, no optional stopping.
    n: int = Field(ge=1)
    deterministic: bool = True
    #: `predicted` when the outcome is stated before the run; `unknown` when it honestly is not
    #: (the predictions then record the expected shape, and disagreement is recorded, not flagged).
    confidence: Literal["predicted", "unknown"]
    predicted: tuple[Prediction, ...] = Field(min_length=1)
    escalate_if: Text
    #: What this cell's prediction still waits on, if anything; a final pre-registration has none.
    pending: Text | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems: list[str] = []
        if self.evidence == "replication" and self.replication_of not in PUBLISHED_REPLICATIONS:
            problems.append(f"replication_of must be one of {sorted(PUBLISHED_REPLICATIONS)}")
        if self.evidence != "replication" and self.replication_of is not None:
            problems.append("only a replication names replication_of")
        if (self.evidence == "new-evidence") != (self.phase == "new-evidence"):
            problems.append("new-evidence cells, and only they, run in the new-evidence phase")
        invariants = [p.invariant for p in self.predicted]
        if len(set(invariants)) != len(invariants):
            problems.append("one prediction per invariant")
        if problems:
            raise ValueError(f"cell {self.id}: {'; '.join(problems)}")
        return self


class InstrumentCheck(_Model):
    """A required check of the instrument that is not a batch: artifact mutants, saver mutants,
    checker determinism. Its evidence (a CI run or a commit) is recorded before the
    pre-registration is final."""

    id: Id
    requirement: Text
    evidence: Text | None = None


class CheckerPin(_Model):
    """The frozen checker every batch runs under (src/crashcourt/checker/FROZEN.md), every hash
    the freeze records spelled out."""

    version: Id
    #: FreezeManifest.checker_sha256: the checker's files.
    checker_sha256: Sha256
    #: sha256 of FROZEN.md's bytes: the checker, the clause registry, the effect models, and the
    #: landmark tables together.
    frozen_md_sha256: Sha256
    registry_sha256: Sha256
    #: Each workload's effect model, by name, as FROZEN.md records it.
    effect_models: dict[Id, Sha256] = Field(min_length=1)
    #: Each audited landmark table, by name, as FROZEN.md records it.
    landmark_tables: dict[Id, Sha256] = Field(min_length=1)


def derive_seed(master_seed: str, cell: str, trial: int) -> str:
    """A trial's seed: sha256 of the JSON array [master_seed, cell, trial], compact, with
    non-ASCII kept. Computed here independently of the runner, so verification checks it."""
    encoded = json.dumps([master_seed, cell, trial], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SeedRule(_Model):
    """How every trial's seed is derived; there is no per-cell or per-trial seed choice."""

    derivation: Literal["sha256(json([master_seed, cell, trial]))"] = (
        "sha256(json([master_seed, cell, trial]))"
    )
    #: The master seed lives in each scenario file, so the scenario hash pins it. A rerun of a cell
    #: is a new batch; a different master seed is a different scenario and needs its own entry.
    master_seed_in: Literal["scenario"] = "scenario"


class Preregistration(_Model):
    v: Literal[1]
    name: Id
    state: Literal["draft", "final"]
    #: The checker this matrix runs under; null only in a draft.
    checker: CheckerPin | None = None
    seeds: SeedRule = SeedRule()
    #: Manifest fields every batch's manifest must equal, by dotted path (`postgres.fsync`).
    environment: dict[str, JsonValue] = Field(min_length=1)
    #: A cell whose INVALID trials exceed this share is excluded and investigated.
    max_invalid_share: float = Field(default=0.02, ge=0, lt=1)
    instrument_checks: tuple[InstrumentCheck, ...] = ()
    cells: tuple[Cell, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _final_is_complete(self) -> Self:
        for name, values in (
            ("cell ids", [c.id for c in self.cells]),
            ("scenario files", [c.scenario for c in self.cells]),
            ("scenario hashes", [c.scenario_sha256 for c in self.cells if c.scenario_sha256]),
        ):
            repeated = sorted({v for v in values if values.count(v) > 1})
            if repeated:
                # A scenario names exactly one cell, so the gate cannot authorize the wrong one.
                raise ValueError(f"{name} repeat: {repeated}")
        if self.state == "final":
            missing = self.missing()
            if missing:
                raise ValueError("a final pre-registration is complete: " + "; ".join(missing))
        return self

    def missing(self) -> list[str]:
        """What a draft still lacks before it can be final."""
        found: list[str] = []
        if self.checker is None:
            found.append("no frozen checker is named")
        for name, value in self.environment.items():
            if value is None:
                found.append(f"environment {name} is not pinned")
        for c in self.cells:
            if c.scenario_sha256 is None:
                found.append(f"cell {c.id}: no scenario hash")
            if c.master_seed is None:
                found.append(f"cell {c.id}: no master seed")
            if c.pending is not None:
                found.append(f"cell {c.id}: pending {c.pending}")
        for check in self.instrument_checks:
            if check.evidence is None:
                found.append(f"instrument check {check.id}: no evidence recorded")
        return found

    def cell(self, cell_id: str) -> Cell:
        for c in self.cells:
            if c.id == cell_id:
                return c
        raise KeyError(cell_id)

    def by_scenario(self, scenario: str) -> Cell:
        """The cell whose scenario file is `scenario` (a repository-relative POSIX path)."""
        for c in self.cells:
            if c.scenario == scenario:
                return c
        raise KeyError(scenario)


class PreregistrationError(ValueError):
    """The file holds no valid pre-registration block."""


def parse(text: str) -> Preregistration:
    """The pre-registration in a research/preregistration.md text: its single JSON block."""
    if text.count(_FENCE) != 1:
        raise PreregistrationError(
            f"expected exactly one {_FENCE} block, found {text.count(_FENCE)}"
        )
    start = text.index(_FENCE) + len(_FENCE)
    end = text.index("```", start)
    try:
        return Preregistration.model_validate_json(text[start:end])
    except ValueError as exc:
        raise PreregistrationError(str(exc)) from exc


def block(prereg: Preregistration) -> str:
    """The JSON block as the file holds it: sorted keys, two-space indent."""
    body = json.dumps(prereg.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
    return f"{_FENCE}\n{body}\n```"


_TABLE_START = "## Cells\n\n"
_TABLE_END = "\n\n## "


def render_table(prereg: Preregistration) -> str:
    """The cell table the prose shows, rendered from the data so the two cannot disagree."""
    rows = [
        "| Cell | Phase | Evidence | Workload | Durability | Fault | Workers | N | Predicted | |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in prereg.cells:
        predicted = "; ".join(f"{p.invariant}: {' or '.join(p.expect)}" for p in c.predicted)
        evidence = c.evidence
        if c.replication_of:
            evidence += f" ({c.replication_of})"
        elif c.control:
            evidence += f" ({c.control})"
        flags = [
            f for f, on in (("unknown", c.confidence == "unknown"), ("pending", c.pending)) if on
        ]
        rows.append(
            f"| {c.id} | {c.phase} | {evidence} | {c.workload} | {c.durability} | {c.fault or '-'} "
            f"| {c.workers} | {c.n} | {predicted} | {', '.join(flags)} |"
        )
    return "\n".join(rows)


def canonical(text: str) -> str:
    """The file with its JSON block in canonical form and its cell table rendered from it."""
    prereg = parse(text)
    start = text.index(_FENCE)
    end = text.index("```", start + len(_FENCE)) + 3
    text = text[:start] + block(prereg) + text[end:]
    table_start = text.index(_TABLE_START) + len(_TABLE_START)
    table_end = text.index(_TABLE_END, table_start)
    return text[:table_start] + render_table(prereg) + text[table_end:]
