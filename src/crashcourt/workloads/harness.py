"""What a workload's nodes use from the harness: node events, attempts, and effects.

Every node runs inside `harness.node(name, config)`:

- The node's attempt comes first, from the sink's counter `node/<thread>/<node>/<turn>`. The
  counter lives outside the worker, so a replacement worker that re-runs the node gets the next
  attempt rather than repeating the first, and so does a node LangGraph re-runs on resume.
- `node.start` names the op, the thread, the node's LangGraph task id, the checkpoint the task
  runs from, and the attempt. The task id is the one LangGraph passes to `put_writes` for the
  task's writes, so the checker can join committed writes to the node that produced them.
- `node.end` follows when the node returns (`outcome` completed), pauses at `interrupt()`
  (`outcome` interrupted), or raises (status fail, with the error).
- An effect is posted through the sink with every field the ledger needs, inside the node that
  causes it, and carries the node's attempt.

The task id and the checkpoint come from the configuration LangGraph gives each task
(`__pregel_task_id`, and `checkpoint_map[""]` for the root graph's checkpoint). Neither is a
public API; langgraph is pinned, and a unit test checks the task id against `put_writes`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Protocol, Self

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp
from pydantic import JsonValue

from crashcourt.failpoints.context import current_op
from crashcourt.history.writer import HistoryWriter
from crashcourt.sink.client import EffectSink, record_effect, trial_of
from crashcourt.sink.ledger import EffectRequest, TrialId

TASK_ID_KEY = "__pregel_task_id"
CHECKPOINT_MAP_KEY = "checkpoint_map"


class Counters(Protocol):
    def next_attempt(self, trial: TrialId, key: str) -> int: ...


class Sink(EffectSink, Counters, Protocol):
    """A `SinkClient`, or a test double over a `Ledger`."""


def payload_hash(payload: Mapping[str, JsonValue]) -> str:
    """sha256 of the payload's canonical JSON: sorted keys, no whitespace."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def attempt_key(thread_id: str, node: str, turn: int) -> str:
    """The sink counter one node execution increments."""
    return f"node/{thread_id}/{node}/{turn}"


class Harness:
    """One worker's harness: its history writer and its sink."""

    def __init__(self, history: HistoryWriter, sink: Sink) -> None:
        self.history = history
        self.sink = sink
        self.trial = trial_of(history)

    def node(self, name: str, config: RunnableConfig, *, turn: int = 0) -> NodeRun:
        return NodeRun(self, name, config, turn)


class NodeRun:
    """One execution of one node: its attempt, its events, and the effects it posts."""

    def __init__(self, harness: Harness, name: str, config: RunnableConfig, turn: int) -> None:
        configurable: Mapping[str, Any] = config.get("configurable") or {}
        checkpoints: Mapping[str, str] = configurable.get(CHECKPOINT_MAP_KEY) or {}
        self.harness = harness
        self.name = name
        self.turn = turn
        self.thread_id = str(configurable["thread_id"])
        self.task_id = str(configurable[TASK_ID_KEY])
        self.checkpoint_seen: str | None = checkpoints.get("")
        self.op_id = current_op()
        self.resuming = bool(configurable.get("__pregel_resuming"))
        self.attempt = 0

    def _fields(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "thread_id": self.thread_id,
            "checkpoint_id": self.checkpoint_seen,
            "task_id": self.task_id,
            "attempt": self.attempt,
        }

    def __enter__(self) -> Self:
        if self.op_id is None:
            raise RuntimeError(f"node {self.name} ran outside an operation")
        key = attempt_key(self.thread_id, self.name, self.turn)
        self.attempt = self.harness.sink.next_attempt(self.harness.trial, key)
        data: dict[str, JsonValue] = {"node": self.name, "turn": self.turn}
        if self.resuming:
            data["resuming"] = True
        self.harness.history.emit("node.start", **self._fields(), data=data)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        data: dict[str, JsonValue] = {"node": self.name, "turn": self.turn}
        if exc is None:
            data["outcome"] = "completed"
            self.harness.history.emit("node.end", **self._fields(), status="ok", data=data)
        elif isinstance(exc, GraphBubbleUp):  # interrupt(): the node pauses, it does not fail
            data["outcome"] = "interrupted"
            self.harness.history.emit("node.end", **self._fields(), status="ok", data=data)
        else:
            data["outcome"] = "raised"
            error = f"{type(exc).__name__}: {exc}"
            self.harness.history.emit(
                "node.end", **self._fields(), status="fail", error=error, data=data
            )

    def post_effect(
        self,
        effect_key: str,
        payload: Mapping[str, JsonValue],
        *,
        data: Mapping[str, JsonValue] | None = None,
    ) -> int:
        """Post one effect caused by this execution; returns its ledger row. A sink error
        propagates: an effect is never retried or skipped quietly."""
        history = self.harness.history
        effect = EffectRequest(
            effect_key=effect_key,
            logical_op=effect_key,
            op_id=str(self.op_id),
            attempt=self.attempt,
            worker=history.proc,
            pid=history.pid,
            thread_id=self.thread_id,
            node=self.name,
            checkpoint_id_seen=self.checkpoint_seen,
            payload_hash=payload_hash(payload),
        )
        return record_effect(history, self.harness.sink, effect, data=data)
