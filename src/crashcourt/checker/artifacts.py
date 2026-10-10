"""The checker's inputs: one trial's scenario facts and its four trusted records.

A trial is judged from per-process histories, the effect ledger, a direct database snapshot, and
the manifest. Nothing here is read from the target's own reports. The scenario facts are what the
orchestrator declared before the trial ran: the workload, the durability mode, the operations it
sent, the failpoint arms, the faults, and the landmark table the arms are verified against.

These models are the checker's side of the trial directory. T-07 writes the directory and
T-10's snapshot dump fills the snapshot; until then the models are built directly (synthetic
histories and artifact mutants). Field names follow T-04 (events), T-06 (failpoints), and T-08
(ledger rows) where those already define them.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from crashcourt.checker.status import EvidenceClass
from crashcourt.manifest import Manifest
from crashcourt.spec.events import Event
from crashcourt.spec.records import Provenance

Name = Annotated[str, StringConstraints(min_length=1)]
Count = Annotated[int, Field(ge=0, strict=True)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

Durability = Literal["sync", "async", "exit"]
#: W1 covers both resume values (approve and reject); the misuse variant posts an effect before
#: interrupt(). W5 is the two-turn continuation workload.
Workload = Literal["w1", "w1-misuse", "w5"]
OpKind = Literal["start", "resume", "turn"]

#: The failpoints of T-06's FailpointSaver (crashcourt.failpoints.registry.POINTS).
Point = Literal[
    "put.before",
    "put.after",
    "put_writes.before",
    "put_writes.after",
    "get_tuple.before",
    "get_tuple.after",
    "list.before",
    "list.after",
]
ArmAction = Literal["none", "pause", "exit", "signal"]

#: The fault-fired table (red-team specification 3.6), one kind per row.
FaultKind = Literal[
    "pause",
    "sigkill",
    "exit",
    "sigstop",
    "lost-ack",
    "reset",
    "delayed-ack",
    "terminate-backend",
    "db-restart",
    "redelivery",
    "second-resume",
]

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class OpSpec(BaseModel):
    """One client request the scenario sends."""

    model_config = _FROZEN

    op_id: Name
    kind: OpKind
    thread_id: Name
    logical_op: Name
    #: The resume value a resume op sends (W1: approve or reject).
    resume: str | None = None

    @model_validator(mode="after")
    def _resume_only_on_resume(self) -> Self:
        if (self.kind == "resume") != (self.resume is not None):
            raise ValueError("a resume op carries its resume value, and only a resume op does")
        return self


class ArmSpec(BaseModel):
    """A failpoint arm, as T-06's Arm, plus the landmark its hit must be verified against."""

    model_config = _FROZEN

    point: Point
    action: ArmAction
    worker: Name
    op_id: Name | None = None
    thread_id: Name | None = None
    occurrence: Annotated[int, Field(ge=1, strict=True)] = 1
    signal: str | None = None
    exit_code: Annotated[int, Field(ge=0, le=255)] | None = None
    #: The landmark id in the scenario's landmark table.
    landmark: Name | None = None


class FaultSpec(BaseModel):
    """One fault the scenario injects, and what identifies it in the records."""

    model_config = _FROZEN

    kind: FaultKind
    #: Index into the scenario's arms: the failpoint that triggers the fault, or the call a
    #: network fault targets.
    arm: Count | None = None
    #: The orchestrator's fault id on fault.inject and fault.heal.
    fault_id: Name | None = None
    worker: Name | None = None
    #: redelivery: the op redelivered; second-resume: the two resume ops in their declared order.
    op_ids: tuple[Name, ...] = ()
    exit_code: Annotated[int, Field(ge=0, le=255)] | None = None


ContextRuleKind = Literal[
    "call_data_equals",
    "call_channels_include",
    "call_task_of_node",
    "after_node_end",
    "before_node_start",
    "no_kind_before",
    "first_call",
    "last_call",
]


class ContextRule(BaseModel):
    """One check of the events around a failpoint hit. The checker holds only these generic
    evaluators; which rules make up a landmark is data, written into the landmark table."""

    model_config = _FROZEN

    rule: ContextRuleKind
    key: str | None = None
    value: JsonValue = None
    node: str | None = None
    kind: str | None = None
    channel: str | None = None


Method = Literal["put", "put_writes", "get_tuple", "list"]


class LandmarkSpec(BaseModel):
    """A saver call, named by its context. An arm at either failpoint of the method (`.before`
    or `.after`) may name it."""

    model_config = _FROZEN

    id: Name
    method: Method
    rules: tuple[ContextRule, ...] = Field(min_length=1)


class LandmarkTable(BaseModel):
    """The audited landmark table a scenario's arms refer to. Derived from real fault-free T-09
    histories and frozen with the checker; never written from guessed occurrence numbers."""

    model_config = _FROZEN

    name: Name
    #: draft until derived from real fault-free histories and audited; hits are verified against
    #: a draft table only in synthetic trials and artifact mutants.
    status: Literal["draft", "audited"]
    landmarks: tuple[LandmarkSpec, ...]

    def get(self, landmark_id: str) -> LandmarkSpec | None:
        return next((spec for spec in self.landmarks if spec.id == landmark_id), None)


class TrialScenario(BaseModel):
    """What the orchestrator declared for this trial before it ran."""

    model_config = _FROZEN

    batch: Name
    cell: Name
    trial: Count
    provenance: Provenance
    #: sha256 of the batch's manifest.json; the trial's manifest copy must match it.
    manifest_sha256: Sha256
    evidence: EvidenceClass
    replication_of: str | None = None
    #: The red-team matrix name of the cell (K1a, N4, ...), when it has one.
    matrix_cell: str | None = None
    workload: Workload
    durability: Durability
    #: The saver class under test, as clauses name it in applies_to.
    target: Name
    threads: tuple[Name, ...] = Field(min_length=1)
    ops: tuple[OpSpec, ...] = Field(min_length=1)
    arms: tuple[ArmSpec, ...] = ()
    faults: tuple[FaultSpec, ...] = ()
    landmarks: LandmarkTable | None = None
    #: The direct, unproxied DSN the snapshot must have used.
    oracle_dsn: Name
    #: The batch's runner type; the trial's manifest must name the same one.
    runner: Name
    #: The effect sink's source hash the batch was run with.
    sink_source_sha256: Sha256 | None = None
    #: Each Toxiproxy proxy and the one worker that reaches Postgres through it (T-07).
    proxies: dict[str, Name] = Field(default_factory=dict[str, Name])

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems: list[str] = []
        op_ids = [op.op_id for op in self.ops]
        if len(set(op_ids)) != len(op_ids):
            problems.append("op ids repeat")
        if any(op.thread_id not in self.threads for op in self.ops):
            problems.append("an op names a thread the scenario does not declare")
        for fault in self.faults:
            if fault.arm is not None and fault.arm >= len(self.arms):
                problems.append(f"fault {fault.kind} names arm {fault.arm}, which does not exist")
            if any(op not in op_ids for op in fault.op_ids):
                problems.append(f"fault {fault.kind} names an op the scenario does not send")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def op(self, op_id: str | None) -> OpSpec | None:
        return next((op for op in self.ops if op.op_id == op_id), None)


class ProcessHistory(BaseModel):
    """One process's history file, as read: its complete events and any unterminated tail."""

    model_config = _FROZEN

    proc: Name
    events: tuple[Event, ...]
    #: Bytes after the last newline: the line a killed process was writing.
    partial_tail: bytes = b""
    #: Set when a complete line failed to parse (crashcourt.history.reader.HistoryError).
    parse_error: str | None = None


class LedgerRow(BaseModel):
    """One effect ledger row, with T-08's columns (crashcourt.sink.ledger)."""

    model_config = _FROZEN

    sink_seq: Annotated[int, Field(ge=1, strict=True)]
    received_mono_ns: int
    received_wall_ns: int
    effect_key: Name
    logical_op: Name
    op_id: Name
    attempt: Annotated[int, Field(ge=1, strict=True)]
    worker: Name
    pid: Count
    thread_id: Name
    node: Name
    checkpoint_id_seen: str | None
    payload_hash: Sha256
    status: Name
    prev_hash: Sha256
    row_hash: Sha256


class LedgerExport(BaseModel):
    model_config = _FROZEN

    rows: tuple[LedgerRow, ...]
    #: The sink's own /ledger/verify answer at export time.
    verify_ok: bool


class SinkHealth(BaseModel):
    """One /health reading of the effect sink."""

    model_config = _FROZEN

    when: Literal["before", "after"]
    hooks_enabled: bool
    source_sha256: Sha256
    #: The trial the sink had open, as batch/cell/trial.
    open_trial: str | None


class CheckpointRow(BaseModel):
    model_config = _FROZEN

    thread_id: Name
    checkpoint_ns: str
    checkpoint_id: Name
    parent_checkpoint_id: str | None
    #: The checkpoint column (JSONB): channel_values holds the inline (primitive) values.
    checkpoint: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    xmin: Name
    #: pg_xact_commit_timestamp(xmin) in nanoseconds since the epoch.
    commit_ns: int | None


class BlobRow(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    thread_id: Name
    checkpoint_ns: str
    channel: Name
    version: Name
    type: Name
    blob: bytes | None
    xmin: Name
    commit_ns: int | None


class WriteRow(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    thread_id: Name
    checkpoint_ns: str
    checkpoint_id: Name
    task_id: Name
    task_path: str
    idx: int
    channel: Name
    type: Name
    blob: bytes | None
    xmin: Name
    commit_ns: int | None


class SnapshotMeta(BaseModel):
    model_config = _FROZEN

    #: The orchestrator history's seq of its last event before the snapshot was taken.
    after_orchestrator_seq: Count
    dsn: Name
    #: SHOW values read at snapshot time (fsync, synchronous_commit, track_commit_timestamp).
    settings: dict[str, str]
    #: pg_postmaster_start_time() in nanoseconds since the epoch.
    postmaster_start_ns: int
    #: Toxics installed on any proxy when the snapshot was taken (from the Toxiproxy API).
    toxics_installed: tuple[str, ...] = ()
    #: The thread ids the dump selected.
    threads: tuple[Name, ...]


class Snapshot(BaseModel):
    model_config = _FROZEN

    meta: SnapshotMeta
    checkpoints: tuple[CheckpointRow, ...] = ()
    blobs: tuple[BlobRow, ...] = ()
    writes: tuple[WriteRow, ...] = ()

    def same_rows(self, other: Snapshot) -> bool:
        return (
            self.checkpoints == other.checkpoints
            and self.blobs == other.blobs
            and self.writes == other.writes
        )


class SnapshotPair(BaseModel):
    """Two full snapshots taken at least two seconds apart after quiescence, and the pre-trial
    emptiness count per thread."""

    model_config = _FROZEN

    first: Snapshot
    second: Snapshot
    pretrial_rows: dict[str, int]


class WorkerReport(BaseModel):
    """What a worker reported at clean shutdown."""

    model_config = _FROZEN

    proc: Name
    #: The failpoint registry's failed set (arms that were due but never acted).
    failpoints_failed: tuple[str, ...] = ()


class HarnessRecord(BaseModel):
    """The orchestrator's own verdict on the trial (T-07's trial.json): whether the harness ran it
    as declared. A protocol violation, a timer-ordered run, or an unexplained worker exit makes it
    invalid, and the checker then makes every invariant INVALID."""

    model_config = _FROZEN

    valid: bool
    invalid_reasons: tuple[str, ...] = ()
    #: Records that are not reasons, such as saver calls in flight when a step ended a worker.
    observations: tuple[str, ...] = ()


class TrialArtifacts(BaseModel):
    """Everything one trial left behind. A field that is None was not written."""

    model_config = _FROZEN

    scenario: TrialScenario
    manifest: Manifest | None
    #: sha256 of the trial's manifest copy.
    manifest_sha256: Sha256 | None
    histories: tuple[ProcessHistory, ...]
    ledger: LedgerExport | None
    sink_health: tuple[SinkHealth, ...] = ()
    snapshots: SnapshotPair | None
    worker_reports: tuple[WorkerReport, ...] = ()
    #: T-07's trial.json verdict; required for a harness run (provenance "run").
    harness: HarnessRecord | None = None
