"""The history event envelope, schema v1. Frozen: a change needs schema v2.

Every process writes one JSONL line per event. A line carries the full envelope, with null for
fields that do not apply, so every line is self-describing.

Kind-specific requirements. The merger derives cross-process causal edges from a few fields, so
events of these kinds must carry them:

- client.*: op_id.
- client.ok, client.fail: optionally data.worker and data.worker_seq, both or neither: the worker
  that answered and the seq of its last event before it sent the result.
- dispatch.send, dispatch.redeliver: op_id; data.to, the worker's proc name.
- worker.start, worker.exit: data.proc, the worker's proc name.
- fp.hit: fp.
- fp.release: data.proc, the paused worker; data.hit_seq, the seq of its fp.hit.
- fp.action: data.hit_seq, the seq of the fp.hit whose action this is. For a pause, the failpoint
  saver writes fp.action only after the orchestrator's fp.release, so the merge orders release
  before action; a writer that logged the action before its release would break that edge.
- effect.request, effect.response, sink.append: effect_key, op_id, attempt.
- sink.append: data.worker, the requesting worker's proc name; data.sink_seq, the ledger row.
- effect.response with status ok: data.sink_seq, the ledger row the sink acknowledged. The merge
  links the append to this response; a failed response names no row and gets no edge.
- fault.inject, fault.heal: fault_id.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION: Literal[1] = 1

EventKind = Literal[
    "client.invoke",
    "client.ok",
    "client.fail",
    "client.info",
    "dispatch.send",
    "dispatch.redeliver",
    "worker.start",
    "worker.exit",
    "node.start",
    "node.end",
    "saver.put.call",
    "saver.put.return",
    "saver.put.raise",
    "saver.put_writes.call",
    "saver.put_writes.return",
    "saver.put_writes.raise",
    "saver.get_tuple.call",
    "saver.get_tuple.return",
    "saver.get_tuple.raise",
    "saver.list.call",
    "saver.list.return",
    "saver.list.raise",
    "fp.hit",
    "fp.release",
    "fp.action",
    "effect.request",
    "effect.response",
    "sink.append",
    "fault.inject",
    "fault.heal",
]
EVENT_KINDS: tuple[str, ...] = get_args(EventKind)

Status = Literal["ok", "fail", "info"]


def _the_integer(value: object) -> object:
    # true and 1.0 compare equal to 1, so Literal[1] alone would accept them.
    if type(value) is not int:
        raise ValueError("the schema version is the integer 1")
    return value


Version = Annotated[Literal[1], BeforeValidator(_the_integer)]

Name = Annotated[str, StringConstraints(min_length=1)]
Count = Annotated[int, Field(ge=0, strict=True)]
Nanoseconds = Annotated[int, Field(strict=True)]
Attempt = Annotated[int, Field(ge=1, strict=True)]


def _data_str(data: dict[str, JsonValue], key: str) -> bool:
    value = data.get(key)
    return isinstance(value, str) and value != ""


def _data_count(data: dict[str, JsonValue], key: str) -> bool:
    value = data.get(key)
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class Event(BaseModel):
    """One history event. Field order is the order of the JSON line.

    Some kinds require extra fields (op_id, fault_id, data.to, data.hit_seq, and others; see
    crashcourt.spec.events). Those rules are enforced when an event is loaded and are not
    expressed in this JSON Schema.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Version
    batch: Name
    cell: Name
    trial: Count
    seed: str
    proc: Name
    pid: Count
    seq: Count
    mono_ns: Nanoseconds
    wall_ns: Nanoseconds
    kind: EventKind
    op_id: str | None = None
    logical_op: str | None = None
    attempt: Attempt | None = None
    effect_key: str | None = None
    thread_id: str | None = None
    checkpoint_ns: str | None = None
    checkpoint_id: str | None = None
    parent_checkpoint_id: str | None = None
    task_id: str | None = None
    fp: str | None = None
    fault_id: str | None = None
    status: Status | None = None
    error: str | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])

    @model_validator(mode="after")
    def _kind_requirements(self) -> Event:
        missing: list[str] = []
        family = self.kind.split(".", 1)[0]
        if family in {"client", "dispatch"} and not self.op_id:
            missing.append("op_id")
        if self.kind in {"client.ok", "client.fail"} and (
            "worker" in self.data or "worker_seq" in self.data
        ):
            if not _data_str(self.data, "worker"):
                missing.append("data.worker")
            if not _data_count(self.data, "worker_seq"):
                missing.append("data.worker_seq")
        if family == "dispatch" and not _data_str(self.data, "to"):
            missing.append("data.to")
        if family == "worker" and not _data_str(self.data, "proc"):
            missing.append("data.proc")
        if self.kind == "fp.hit" and not self.fp:
            missing.append("fp")
        if self.kind == "fp.release":
            if not _data_str(self.data, "proc"):
                missing.append("data.proc")
            if not _data_count(self.data, "hit_seq"):
                missing.append("data.hit_seq")
        if self.kind == "fp.action" and not _data_count(self.data, "hit_seq"):
            missing.append("data.hit_seq")
        if family in {"effect", "sink"}:
            missing.extend(
                name
                for name in ("effect_key", "op_id", "attempt")
                if getattr(self, name) in (None, "")
            )
        if self.kind == "sink.append":
            if not _data_str(self.data, "worker"):
                missing.append("data.worker")
            if not _data_count(self.data, "sink_seq"):
                missing.append("data.sink_seq")
        if (
            self.kind == "effect.response"
            and self.status == "ok"
            and not _data_count(self.data, "sink_seq")
        ):
            missing.append("data.sink_seq")
        if family == "fault" and not self.fault_id:
            missing.append("fault_id")
        if missing:
            raise ValueError(f"{self.kind} requires {', '.join(missing)}")
        return self

    def to_line(self) -> str:
        """The event as one compact JSON line, newline included."""
        return self.model_dump_json() + "\n"
