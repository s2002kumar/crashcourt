"""research/preregistration.md: what a batch will run and what it is predicted to show, fixed in git
before it runs.

The file is prose for people and one fenced JSON block for the gate, the same arrangement as the
checker's FROZEN.md. The JSON block is the binding part: the frozen checker it runs under, the
environment it must run in, the seed rule, and every cell with its scenario hash, its number of
trials, its phase, and its predicted outcome per invariant. The commit that last changed this file
is the pre-registration commit; it must be an ancestor of every batch it covers, and it must come
after the checker's freeze commit (crashcourt.experiment.gate).

A draft may leave the checker, the builder, the environment, and any cell's scenario hash unset.
Finalizing fills everything in, sets `state` to `final`, and is the founder's commit. A final
pre-registration can authorize any of its cells. A draft can authorize only an instrument cell
that is complete on its own (scenario hash, master seed, nothing pending) under complete pins (the
frozen checker, the builder, every environment value); it never authorizes new evidence
(`Preregistration.refusals`). Such a batch can never count toward new evidence: that needs every
instrument cell as predicted under the same pre-registration commit as the new-evidence cell, and
a final pre-registration is a later commit than any draft (crashcourt.experiment.aggregate).

The builder. The code that turns a batch's records into the checker's input
(crashcourt.orchestrator.declared) is outside the frozen checker, so the block pins its file by
content hash; the gate and verification refuse a batch whose pre-registration or run commit holds
any other version of it.

Phases. `instrument` cells (controls and published replications) run first; `new-evidence` cells
are refused until every instrument cell of the same pre-registration, checker, and environment has
come out as predicted.

Tranches. A pre-registration may declare a `tranche`: the cells it is being finalized for. Every
other cell is `deferred`, with a reason, and stays listed with its predictions: it is never
authorized, under a draft or a final, and it needs no scenario hash or seed for the file to be
final. `final` then means final for the tranche and nothing else. The instrument cells that gate
new evidence are the tranche's. Widening the tranche is a change to this file, so a new
pre-registration commit, under which the instrument cells run again; batches of different
commits never pool.

Observations. Besides its outcomes, a cell may predict what it observes: effect ledger rows, a
record in checker.json that is not an outcome, or whether an invariant showed an anomaly at all.
Each is matched in every valid trial, independently of how the anomaly was classified, so a
replication still checks the published shape when its outcome is GAP or nothing is scored.

The mitigation contract. A cell of the N8 mitigation study also predicts what the mitigation
contract (crashcourt.mitigation.contract) measures in every valid trial (`contract`), judged
beside the frozen checker's outcomes and never in their place. The block then pins the contract's
file and the mechanisms it judges (`mitigation`, by content hash, like the builder): a batch whose
pre-registration or run commit holds any other version of them is refused.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from crashcourt.spec.records import OUTCOMES

PREREGISTRATION_PATH = PurePosixPath("research/preregistration.md")
#: The TrialScenario builder: a batch's records to the checker's facts (T-07's side of T-10).
BUILDER_PATH = "src/crashcourt/orchestrator/declared.py"
#: The mitigation contract and the mechanisms it judges, pinned together for the N8 study.
MITIGATION_PATHS: tuple[str, ...] = (
    "src/crashcourt/mitigation/contract.py",
    "src/crashcourt/mitigation/fence.py",
    "src/crashcourt/sink/ledger.py",
)
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


class LedgerRows(_Model):
    """The effect ledger's rows for one effect in a trial (ledger.json, the oracle's export): how
    many, and their attempt numbers."""

    source: Literal["ledger"]
    #: The effect: the effect key up to its first colon (`charge` for `charge:<thread>`).
    effect: Id
    rows: int = Field(ge=0)
    #: The rows' attempts in ascending order; omitted when only the count is predicted.
    attempts: tuple[Annotated[int, Field(ge=1)], ...] | None = None
    #: The rows' statuses in the sink's order (`applied`, or `duplicate` under an idempotent
    #: sink); omitted when the statuses are not predicted.
    statuses: tuple[Id, ...] | None = None
    note: Text | None = None

    @model_validator(mode="after")
    def _attempts(self) -> Self:
        if self.attempts is not None and (
            len(self.attempts) != self.rows or list(self.attempts) != sorted(self.attempts)
        ):
            raise ValueError(f"ledger {self.effect}: attempts lists each row's, in order")
        if self.statuses is not None and len(self.statuses) != self.rows:
            raise ValueError(f"ledger {self.effect}: statuses lists each row's, in sink order")
        return self

    def label(self) -> str:
        attempts = f", attempts {list(self.attempts)}" if self.attempts is not None else ""
        statuses = f", statuses {list(self.statuses)}" if self.statuses is not None else ""
        return f"ledger {self.effect}: {counted(self.rows, 'row')}{attempts}{statuses}"


class CheckerRecord(_Model):
    """Records in the trial's checker.json `observations` that begin with this text, which are
    not outcomes (an accepted run with no durable record, for one)."""

    source: Literal["checker"]
    starts_with: Text
    count: int = Field(ge=0)
    note: Text | None = None

    def label(self) -> str:
        return f"checker observation {self.starts_with!r}: {self.count}"


class AnomalySeen(_Model):
    """Whether the checker found an anomaly on an invariant, whatever its class: any of EXPECTED,
    GAP, VIOLATION_CANDIDATE, or VIOLATION, which differ only in the clause."""

    source: Literal["anomaly"]
    invariant: Id
    present: bool
    note: Text | None = None

    def label(self) -> str:
        return f"anomaly on {self.invariant}: {'present' if self.present else 'absent'}"


#: What a cell predicts it observes, apart from any outcome: matched in every valid trial.
Observation = Annotated[LedgerRows | CheckerRecord | AnomalySeen, Field(discriminator="source")]


def _subject(o: LedgerRows | CheckerRecord | AnomalySeen) -> tuple[str, str]:
    match o:
        case LedgerRows():
            return (o.source, o.effect)
        case CheckerRecord():
            return (o.source, o.starts_with)
        case AnomalySeen():
            return (o.source, o.invariant)


class StalePrediction(_Model):
    """What the contract measures of the run an op was taken away from."""

    #: It went on after the takeover (false for a run that was killed, or never released).
    resumed: bool
    #: At least this many checkpoint puts and task writes ended after it resumed.
    attempted_min: int = Field(default=0, ge=0)
    #: None of them became durable.
    none_durable: bool
    #: Every one of them was fenced (and there was at least one).
    every_attempt_fenced: bool


class ContractPrediction(_Model):
    """What the mitigation contract (crashcourt.mitigation.contract) is predicted to measure in
    every valid trial of a cell of the N8 mitigation study."""

    #: A checkpoint of the thread with two or more children.
    fork: bool
    #: The head (the largest checkpoint id) was written by the run the client's answer came from.
    acknowledged_is_head: bool
    #: The attempts the head's `executed` channel names.
    head_attempts: tuple[Annotated[int, Field(ge=1)], ...]
    #: The stale run; None when the cell redelivers nothing.
    stale: StalePrediction | None
    #: The effect requests any worker made for the logical op, and the ledger's rows by status.
    requested: int = Field(ge=0)
    applied: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    logical: Literal["EXACTLY_ONCE", "DUPLICATED", "LOST", "NONE"]
    #: With exactly one applied row: whether the head's state names its attempt.
    attempt_consistency: Literal["CONSISTENT", "INCONSISTENT", "NOT_APPLICABLE"]
    #: The thread's owner epoch is the acknowledged run's; None when the cell has no fence.
    fence_consistent: bool | None
    note: Text | None = None


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
    #: The worker processes the scenario declares (its `workers:` list), a replacement that takes
    #: a redelivered op over included. The gate and verification refuse a scenario that declares
    #: another number.
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
    #: What the cell shows apart from its outcomes. A replication names the published observation
    #: here, so it is checked even when the outcome is GAP or nothing is scored.
    observations: tuple[Observation, ...] = ()
    escalate_if: Text
    #: For a cell of the N8 mitigation study: what the mitigation contract measures.
    contract: ContractPrediction | None = None
    #: What this cell's prediction still waits on, if anything; a final pre-registration has none.
    pending: Text | None = None
    #: Why the cell is outside the pre-registration's tranche. A deferred cell keeps its entry
    #: and its predictions, needs no scenario hash or seed, and is never authorized.
    deferred: Text | None = None

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
        if self.evidence == "replication" and not self.observations:
            problems.append("a replication names the published observation it reproduces")
        subjects = [_subject(o) for o in self.observations]
        if len(set(subjects)) != len(subjects):
            problems.append("one observation per effect, checker record, or invariant")
        if problems:
            raise ValueError(f"cell {self.id}: {'; '.join(problems)}")
        return self


class InstrumentCheck(_Model):
    """A required check of the instrument that is not a batch: artifact mutants, saver mutants,
    checker determinism. Its evidence (a CI run or a commit) is recorded before the
    pre-registration is final. For C5 the evidence is the repository path of its controls report,
    which the gate reads and judges before any new evidence (crashcourt.experiment.controls)."""

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


def counted(n: int, noun: str) -> str:
    """`1 row`, `2 rows`."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


class BuilderPin(_Model):
    """The TrialScenario builder every batch's trials are classified through, by the sha256 of
    its file's bytes. It is not in FROZEN.md, so it is pinned here."""

    path: Literal["src/crashcourt/orchestrator/declared.py"] = BUILDER_PATH
    sha256: Sha256


class MitigationPin(_Model):
    """The mitigation contract and the mechanisms it judges (the fence and the sink's ledger),
    each by the sha256 of its file's bytes, for every cell that predicts the contract."""

    #: The contract's name, as crashcourt.mitigation.contract.CONTRACT states it.
    contract: Id
    files: dict[Text, Sha256] = Field(min_length=1)

    @model_validator(mode="after")
    def _the_pinned_files(self) -> Self:
        if tuple(self.files) != MITIGATION_PATHS:
            raise ValueError(f"the mitigation pin names {list(MITIGATION_PATHS)}, in that order")
        return self


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


class Tranche(_Model):
    """The cells this pre-registration is finalized for, by name; the rest are deferred."""

    name: Id
    #: Why these cells and no others, before any of them runs.
    note: Text


class Preregistration(_Model):
    v: Literal[1]
    name: Id
    state: Literal["draft", "final"]
    #: The tranche `final` is final for; None finalizes the whole matrix at once.
    tranche: Tranche | None = None
    #: The checker this matrix runs under; null only in a draft.
    checker: CheckerPin | None = None
    #: The TrialScenario builder the checker's input comes from; null only in a draft.
    builder: BuilderPin | None = None
    #: The mitigation contract and mechanisms; required once a tranche cell predicts the contract.
    mitigation: MitigationPin | None = None
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
        if self.tranche is None and any(c.deferred for c in self.cells):
            raise ValueError("a cell is deferred, but the pre-registration declares no tranche")
        if self.tranche is not None and not any(c.deferred is None for c in self.cells):
            raise ValueError(f"tranche {self.tranche.name} holds no cell")
        if self.state == "final":
            missing = self.missing()
            if missing:
                raise ValueError("a final pre-registration is complete: " + "; ".join(missing))
        return self

    def in_tranche(self, cell: Cell) -> bool:
        """Whether the cell is one this pre-registration is finalized for."""
        return cell.deferred is None

    @property
    def tranche_cells(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.deferred is None)

    @property
    def deferred_cells(self) -> tuple[Cell, ...]:
        return tuple(c for c in self.cells if c.deferred is not None)

    def _pins_missing(self) -> list[str]:
        found: list[str] = []
        if self.checker is None:
            found.append("no frozen checker is named")
        if self.builder is None:
            found.append("no TrialScenario builder is pinned")
        if self.mitigation is None and any(c.contract is not None for c in self.tranche_cells):
            found.append("a tranche cell predicts the mitigation contract, but none is pinned")
        for name, value in self.environment.items():
            if value is None:
                found.append(f"environment {name} is not pinned")
        return found

    @staticmethod
    def _cell_missing(c: Cell) -> list[str]:
        found: list[str] = []
        if c.scenario_sha256 is None:
            found.append(f"cell {c.id}: no scenario hash")
        if c.master_seed is None:
            found.append(f"cell {c.id}: no master seed")
        if c.pending is not None:
            found.append(f"cell {c.id}: pending {c.pending}")
        return found

    def missing(self) -> list[str]:
        """What a draft still lacks before it can be final: for the tranche's cells, when one
        is declared, and for every cell otherwise."""
        found = self._pins_missing()
        for c in self.tranche_cells:
            found.extend(self._cell_missing(c))
        for check in self.instrument_checks:
            if check.evidence is None:
                found.append(f"instrument check {check.id}: no evidence recorded")
        return found

    def refusals(self, cell: Cell) -> list[str]:
        """Why this pre-registration, as it stands, cannot authorize a batch of `cell`; empty
        when it can. A deferred cell is never authorized. A final one can authorize every cell
        of its tranche. A draft can authorize an instrument cell that is complete under complete
        pins, and nothing else."""
        if cell.deferred is not None:
            name = self.tranche.name if self.tranche is not None else "the tranche"
            return [f"cell {cell.id} is deferred from {name}: {cell.deferred}"]
        if self.state == "final":
            return []
        if cell.phase != "instrument":
            return [f"the pre-registration is a draft; new-evidence cell {cell.id} needs it final"]
        found = [f"the pre-registration is a draft and {m}" for m in self._pins_missing()]
        found.extend(f"the pre-registration is a draft and {m}" for m in self._cell_missing(cell))
        return found

    def runnable(self) -> list[str]:
        """The cells this pre-registration, as it stands, can authorize."""
        return [c.id for c in self.cells if not self.refusals(c)]

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
        "| Cell | Phase | Evidence | Workload | Durability | Fault | Workers | N | Predicted "
        "| Observed | |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in prereg.cells:
        predicted = "; ".join(f"{p.invariant}: {' or '.join(p.expect)}" for p in c.predicted)
        observed = "; ".join(o.label() for o in c.observations) or "-"
        evidence = c.evidence
        if c.replication_of:
            evidence += f" ({c.replication_of})"
        elif c.control:
            evidence += f" ({c.control})"
        flags = [
            f
            for f, on in (
                ("unknown", c.confidence == "unknown"),
                ("contract", c.contract is not None),
                ("pending", c.pending),
                ("deferred", c.deferred),
            )
            if on
        ]
        rows.append(
            f"| {c.id} | {c.phase} | {evidence} | {c.workload} | {c.durability} | {c.fault or '-'} "
            f"| {c.workers} | {c.n} | {predicted} | {observed} | {', '.join(flags)} |"
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
