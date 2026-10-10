"""Layer A's output: what a predicate observed, and the witness facts Layer B may read.

A predicate says ANOMALY, NO_ANOMALY, or NOT_EXERCISED. It never names an outcome or a clause.
Layer B (crashcourt.checker.classify) reads only observations: the anomalies, their evidence
pointers, and the named witnesses below, each held or not, with pointers to what witnessed it.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["ANOMALY", "NO_ANOMALY", "NOT_EXERCISED"]

#: More than one writer, or more than one resume, on the thread (the LGP-DT disclaimer).
DISCLAIMED = "disclaimed"
#: A harness-originated event on the thread may explain the anomaly: a dispatch.redeliver, or a
#: second client.invoke on the same logical op.
HARNESS_EXPLAINED = "harness_explained"
#: The writer of a missing checkpoint exited with a signal or a non-zero status before quiescence.
WRITER_CRASHED = "writer_crashed"
#: A stop while the node that produced the earlier attempt was running.
STOP_WHILE_RUNNING = "stop_while_running"
#: The later attempt came from a dispatch after that stop.
RESUME_AFTER_STOP = "resume_after_stop"
#: The thread's writer set is sequential and no worker was stopped while another was dispatched.
SINGLE_WRITER = "single_writer"
#: The effect model places the effect before interrupt() in its node.
BEFORE_INTERRUPT_IN_MODEL = "before_interrupt_in_model"
#: An INTERRUPT write for the producing task (snapshot row or put_writes call).
INTERRUPT_WITNESSED = "interrupt_witnessed"
#: The later attempt belongs to a resume op, after the interrupt.
RESUME_AFTER_INTERRUPT = "resume_after_interrupt"
#: The producing task's regular writes were committed before its attempt was interrupted.
COMMITTED_BEFORE_STOP = "committed_before_stop"
#: Any stop of a worker on the thread is witnessed (used to flag harness investigations).
STOP_WITNESSED = "stop_witnessed"
#: The producing node had ended (its node.end) and its task's regular writes had not committed
#: when its worker stopped: not "running" (founder ruling of 2026-10-10); pending-write
#: semantics own the re-run.
WRITES_PENDING_AT_STOP = "writes_pending_at_stop"


class Witness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    held: bool
    evidence: tuple[str, ...] = ()


class Anomaly(BaseModel):
    """One anomalous item: a missing checkpoint, a duplicate attempt, an orphaned row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: tuple[str, ...] = Field(min_length=1)
    sub_reason: str
    #: Which case of the invariant this is, where the clause depends on it (I2, I4: exception or
    #: crash).
    case: str | None = None
    witnesses: tuple[Witness, ...] = ()

    def held(self, name: str) -> bool:
        return any(w.name == name and w.held for w in self.witnesses)

    def witness(self, name: str) -> Witness | None:
        return next((w for w in self.witnesses if w.name == name), None)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invariant: str
    status: Status
    #: For NOT_EXERCISED: the witness that was missing.
    missing: str | None = None
    anomalies: tuple[Anomaly, ...] = ()
    #: For NO_ANOMALY, the case the exercised window belongs to (I2, I4).
    case: str | None = None
    #: Trial-level witnesses for NO_ANOMALY (DISCLAIMED, at least).
    witnesses: tuple[Witness, ...] = ()
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.status == "ANOMALY") != bool(self.anomalies):
            raise ValueError("an observation has anomalies exactly when its status is ANOMALY")
        if (self.status == "NOT_EXERCISED") != (self.missing is not None):
            raise ValueError("NOT_EXERCISED names the missing witness, and only it does")
        return self

    def held(self, name: str) -> bool:
        return any(w.name == name and w.held for w in self.witnesses)


def not_exercised(invariant: str, missing: str) -> Observation:
    return Observation(invariant=invariant, status="NOT_EXERCISED", missing=missing)


def observed(
    invariant: str,
    anomalies: list[Anomaly],
    *,
    witnesses: tuple[Witness, ...] = (),
    case: str | None = None,
    notes: tuple[str, ...] = (),
) -> Observation:
    return Observation(
        invariant=invariant,
        status="ANOMALY" if anomalies else "NO_ANOMALY",
        anomalies=tuple(anomalies),
        witnesses=witnesses,
        case=None if anomalies else case,
        notes=notes,
    )
