"""Layer B's table: for each invariant, the clauses that may carry an outcome, with their grades.

Polarity comes from the clause registry at classification time, never from this table: a clause
the registry marks `describes` cannot carry PASS or VIOLATION_CANDIDATE however this table grades
it. The grades are the red-team specification's (section 2):

- G3: the text states the outcome ("ensures", "must", "does not") in the user's situation.
- G2: the text states a mechanism the outcome follows from, in a different situation; carries
  PASS and VIOLATION_CANDIDATE only with the caveat recorded in `reason`.
- G1: implementer guidance or a description; carries nothing.
- P: a permission with conditions; carries EXPECTED when every condition is witnessed.

A disclaimed scenario (LGP-DT: more than one writer or more than one resume on a thread) makes
any anomaly GAP. Pending writes (founder decision 2): the registry splits the page's two sentences
into LG-PW-EXC (guarantees: a node failed in the super-step) and LG-PW-CRASH (describes: the writes
are "already durable"), so a crash never inherits the guarantee and carries no verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from crashcourt.checker.artifacts import Durability
from crashcourt.checker.invariants.writes import CRASH, EXCEPTION
from crashcourt.checker.observations import (
    BEFORE_INTERRUPT_IN_MODEL,
    COMMITTED_BEFORE_STOP,
    INTERRUPT_WITNESSED,
    RESUME_AFTER_INTERRUPT,
    RESUME_AFTER_STOP,
    SINGLE_WRITER,
    STOP_WHILE_RUNNING,
    STOP_WITNESSED,
    WRITER_CRASHED,
    WRITES_PENDING_AT_STOP,
)

Grade = Literal["G3", "G2", "G1", "P", "D"]
#: Grades that may carry PASS and VIOLATION_CANDIDATE (G2 only with its caveat).
CARRYING: frozenset[str] = frozenset({"G3", "G2"})

SYNC_G2_CAVEAT = (
    "G2 caveat: LG-DUR-SYNC says when the write happens, not that an acknowledged write "
    "survives or that the final write precedes the return; the escalation chain must settle "
    "this reading before any VIOLATION"
)
PENDING_AT_STOP = (
    "its node had ended and its writes had not committed when its worker stopped, so it was not "
    "running (founder ruling, 2026-10-10): pending-write semantics own the re-run, LG-PW-CRASH "
    "describes them, and no clause permits it"
)
SYNC_READING = (
    "reading: 'next step' includes the first tick after the input checkpoint, and 'writes' "
    "means the saver's put returned; the escalation chain must settle both before any VIOLATION"
)


@dataclass(frozen=True)
class ClauseUse:
    clause: str
    grade: Grade
    #: The durability the clause speaks of; None for every mode.
    durability: Durability | None = None
    #: Required in `reason` whenever a G2 clause carries an outcome.
    caveat: str | None = None
    #: A reading the outcome rests on, recorded in `reason`.
    reading: str | None = None


@dataclass(frozen=True)
class PermitRule:
    use: ClauseUse
    #: Every one of these witnesses must hold for EXPECTED.
    requires: tuple[str, ...]
    label: str


@dataclass(frozen=True)
class GuaranteeRule:
    #: Candidate clauses per case (None when the invariant has one case); the first one the
    #: registry holds is used.
    cases: Mapping[str | None, tuple[ClauseUse, ...]]
    #: Witnesses an anomaly must hold for the clause to forbid it.
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvariantRules:
    invariant: str
    permits: tuple[PermitRule, ...] = ()
    guarantee: GuaranteeRule | None = None
    #: Clauses that describe the behavior; recorded for context, never carry an outcome.
    describes: tuple[ClauseUse, ...] = ()
    fallback: str = "no clause covers the anomaly"
    #: Flag a harness investigation when the fallback GAP is reached...
    investigate: bool = False
    #: ...unless this witness holds.
    investigate_unless: str | None = None
    #: Notes the fallback GAP's reason carries when the named witness holds.
    fallback_notes: Mapping[str, str] = field(default_factory=dict[str, str])
    #: Check the disclaimer before the permissions (I7: "Multi-writer or conflicting resume: GAP
    #: (LGP-DT)"). I4 keeps its fixed cause order, (a) and (b) before (c).
    disclaim_first: bool = False


_LATEST = (ClauseUse("LG-LATEST", "G1"),)
_PENDING_WRITES: Mapping[str | None, tuple[ClauseUse, ...]] = {
    # No crash witnessed: the pending-writes guarantee applies (I4's "PASS (no duplicates)").
    None: (ClauseUse("LG-PW-EXC", "G3"),),
    EXCEPTION: (ClauseUse("LG-PW-EXC", "G3"),),
    CRASH: (ClauseUse("LG-PW-CRASH", "G1"),),
}
_RERUN_AFTER_STOP = PermitRule(
    ClauseUse("LG-DE-RERUN", "P"),
    (STOP_WHILE_RUNNING, RESUME_AFTER_STOP, SINGLE_WRITER),
    "a stop while the producing node was running, then a resume on a sequential writer set",
)

RULES: dict[str, InvariantRules] = {
    r.invariant: r
    for r in (
        InvariantRules(
            "I1a",
            guarantee=GuaranteeRule(
                {None: (ClauseUse("LG-DUR-SYNC", "G3", "sync", reading=SYNC_READING),)}
            ),
        ),
        InvariantRules(
            "I1b",
            permits=(
                PermitRule(
                    ClauseUse("LG-DUR-ASYNC", "P", "async"),
                    (WRITER_CRASHED,),
                    "the checkpoint's writer crashed under async",
                ),
            ),
            guarantee=GuaranteeRule(
                {None: (ClauseUse("LG-DUR-SYNC", "G2", "sync", caveat=SYNC_G2_CAVEAT),)}
            ),
        ),
        InvariantRules(
            "I2",
            guarantee=GuaranteeRule(_PENDING_WRITES),
            fallback="no guaranteeing clause covers this case of pending writes",
        ),
        InvariantRules(
            "I4",
            permits=(
                _RERUN_AFTER_STOP,
                PermitRule(
                    ClauseUse("LG-INT-RERUN", "P"),
                    (BEFORE_INTERRUPT_IN_MODEL, INTERRUPT_WITNESSED, RESUME_AFTER_INTERRUPT),
                    "an effect before interrupt() repeated by the resume",
                ),
            ),
            guarantee=GuaranteeRule(_PENDING_WRITES, requires=(COMMITTED_BEFORE_STOP,)),
            fallback="duplicate with no documented cause",
            investigate=True,
            investigate_unless=STOP_WITNESSED,
            fallback_notes={WRITES_PENDING_AT_STOP: PENDING_AT_STOP},
        ),
        InvariantRules(
            "I5a",
            describes=_LATEST,
            fallback="no guaranteeing clause covers lineage shape",
        ),
        InvariantRules(
            "I5b",
            describes=_LATEST,
            fallback="no guaranteeing clause covers lineage shape",
        ),
        InvariantRules(
            "I5c",
            describes=_LATEST,
            fallback="no guaranteeing clause covers lineage shape",
        ),
        InvariantRules(
            "I6a",
            describes=_LATEST,
            fallback="no guaranteeing clause covers which checkpoint is the head",
        ),
        InvariantRules(
            "I6b",
            describes=_LATEST,
            fallback="no guaranteeing clause covers which checkpoint is the head",
        ),
        InvariantRules(
            "I7a",
            permits=(_RERUN_AFTER_STOP,),
            fallback="no clause covers an orphaned effect",
            investigate=True,
            investigate_unless=STOP_WITNESSED,
            disclaim_first=True,
            fallback_notes={WRITES_PENDING_AT_STOP: PENDING_AT_STOP},
        ),
        InvariantRules("I7b", fallback="no clause covers a phantom effect"),
        InvariantRules("I7c", fallback="no clause covers approval and effect agreement"),
        InvariantRules(
            "I8",
            fallback="no clause covers resume-value integrity",
            investigate=True,
        ),
        InvariantRules(
            "I11",
            guarantee=GuaranteeRule(
                {None: (ClauseUse("LG-DUR-SYNC", "G2", "sync", caveat=SYNC_G2_CAVEAT),)}
            ),
        ),
    )
}
