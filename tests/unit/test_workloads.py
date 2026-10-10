"""W1 and W5 run fault-free on a real graph: effects, attempts, saved state, and node events.

The graphs run on an in-memory saver behind the failpoint wrapper (which records the saver
events), with a sink double over T-08's ledger, inside an operation as a worker runs them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from crashcourt.failpoints.context import operation
from crashcourt.failpoints.saver import wrap
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.sink.ledger import EffectRequest, Ledger, TrialId
from crashcourt.spec.events import Event
from crashcourt.workloads import w1_approval, w5_continuation
from crashcourt.workloads.harness import Harness

RUN = RunIdentity("b-unit", "cell", 1, "seed")
TRIAL = TrialId("b-unit", "cell", 1, "seed")
NULL_TASK = "00000000-0000-0000-0000-000000000000"


class LedgerSink:
    """The sink's two calls, straight to a ledger file."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def post_effect(self, trial: TrialId, effect: EffectRequest) -> dict[str, Any]:
        assert trial == TRIAL
        return {"sink_seq": self.ledger.append(effect)["sink_seq"]}

    def next_attempt(self, trial: TrialId, key: str) -> int:
        assert trial == TRIAL
        return self.ledger.next_attempt(key)


@dataclass
class Lab:
    graph: Any
    history: HistoryWriter
    ledger: Ledger
    saver: InMemorySaver

    async def op(self, op_id: str, thread: str, payload: Any) -> Any:
        config = {"configurable": {"thread_id": thread}}
        with operation(op_id):
            return await self.graph.ainvoke(payload, config, durability="sync")

    def run(self, *ops: tuple[str, str, Any]) -> list[Any]:
        async def go() -> list[Any]:
            return [await self.op(*op) for op in ops]

        return asyncio.run(go())

    def events(self) -> tuple[Event, ...]:
        return read_history(self.history.path).events

    def state(self, thread: str) -> dict[str, Any]:
        found = self.saver.get_tuple({"configurable": {"thread_id": thread}})
        assert found is not None
        return dict(found.checkpoint["channel_values"])


@contextmanager
def lab(tmp_path: Path, build: Callable[..., Any]) -> Iterator[Lab]:
    history = HistoryWriter(tmp_path / "w1.jsonl", run=RUN, proc="w1")
    ledger = Ledger(tmp_path / "ledger.sqlite", TRIAL)
    saver = InMemorySaver()
    graph = build(wrap(saver, history=history), Harness(history, LedgerSink(ledger)))
    try:
        yield Lab(graph, history, ledger, saver)
    finally:
        ledger.close()
        history.close()


def summary(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def w1_ops(decision: str, thread: str = "t1") -> list[tuple[str, str, Any]]:
    return [
        ("start-1", thread, {"order": "order-17"}),
        ("resume-1", thread, Command(resume=decision)),
    ]


def rows(ledger: Ledger) -> list[tuple[str, int, str, str]]:
    return [(r["effect_key"], r["attempt"], r["node"], r["op_id"]) for r in ledger.rows()]


def test_w1_approve_fires_exactly_one_charge_and_saves_the_decision(tmp_path: Path) -> None:
    with lab(tmp_path, w1_approval.build) as w1:
        first, second = w1.run(*w1_ops("approve"))
        assert summary(w1_approval.summarize(first))["interrupted"] is True
        assert rows(w1.ledger) == [("charge:t1", 1, "execute", "resume-1")]
        state = w1.state("t1")
        assert state["decision"] == "approve" and state["executed"] == [["charge:t1", 1]]
        assert w1_approval.summarize(second) == {
            "order": "order-17",
            "plan": state["plan"],
            "resume": "approve",
            "decision": "approve",
            "executed": [["charge:t1", 1]],
            "interrupted": False,
        }
        assert "attempt 1" in state["plan"]  # the scripted model names its attempt


def test_w1_reject_fires_no_effect_and_saves_the_decision(tmp_path: Path) -> None:
    with lab(tmp_path, w1_approval.build) as w1:
        w1.run(*w1_ops("reject"))
        assert rows(w1.ledger) == []
        state = w1.state("t1")
        assert state["decision"] == "reject" and state["executed"] == []
        assert not any(e.kind.startswith("effect.") for e in w1.events())


def test_the_charge_request_records_the_decision_it_acted_on(tmp_path: Path) -> None:
    with lab(tmp_path, w1_approval.build) as w1:
        w1.run(*w1_ops("approve"))
        [request] = [e for e in w1.events() if e.kind == "effect.request"]
        assert request.data["decision"] == "approve"
        assert (request.effect_key, request.op_id, request.attempt) == ("charge:t1", "resume-1", 1)
        [response] = [e for e in w1.events() if e.kind == "effect.response"]
        assert response.status == "ok" and response.data["sink_seq"] == 1
        # The checkpoint the execute task ran from is the one its effect names.
        [start] = [e for e in w1.events() if e.kind == "node.start" and e.data["node"] == "execute"]
        assert request.checkpoint_id == start.checkpoint_id is not None


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_w1_misuse_fires_precheck_before_the_pause_and_again_on_resume(
    tmp_path: Path, decision: str
) -> None:
    with lab(tmp_path, w1_approval.build_misuse) as w1:
        w1.run(*w1_ops(decision))
        prechecks = [r for r in rows(w1.ledger) if r[0] == "precheck:t1"]
        assert prechecks == [
            ("precheck:t1", 1, "approval", "start-1"),
            ("precheck:t1", 2, "approval", "resume-1"),
        ]
        charges = [r for r in rows(w1.ledger) if r[0] == "charge:t1"]
        assert charges == (
            [("charge:t1", 1, "execute", "resume-1")] if decision == "approve" else []
        )
        assert w1.state("t1")["decision"] == decision


def test_every_node_event_names_its_task_and_its_writes_name_the_same_task(
    tmp_path: Path,
) -> None:
    with lab(tmp_path, w1_approval.build_misuse) as w1:
        w1.run(*w1_ops("approve"))
        events = w1.events()
        nodes = [e for e in events if e.kind in ("node.start", "node.end")]
        assert nodes and all(e.task_id for e in nodes) and all(e.attempt for e in nodes)
        tasks: dict[str, set[str | None]] = {}
        for e in nodes:
            tasks.setdefault(str(e.data["node"]), set()).add(e.task_id)
        # Each node's task id is stable across its attempts (approval runs twice).
        assert all(len(ids) == 1 for ids in tasks.values()), tasks
        writes = [e for e in events if e.kind == "saver.put_writes.call"]
        # Each node's writes (its task path ends with the node) carry that node's task id. The
        # other writes are the input task's (__start__) and the resume value's (the null task).
        by_node: dict[str, set[str | None]] = {}
        for e in writes:
            node = str(e.data["task_path"]).rsplit(", ", 1)[-1]
            by_node.setdefault(node, set()).add(e.task_id)
        assert set(by_node) == {*tasks, "__start__", ""}
        assert by_node[""] == {NULL_TASK}
        for node, ids in tasks.items():
            assert by_node[node] == ids, node
        # The interrupted attempt ends as interrupted, the resumed one as completed.
        approval_ends = [
            e.data["outcome"]
            for e in nodes
            if e.kind == "node.end" and e.data["node"] == "approval"
        ]
        assert approval_ends == ["interrupted", "completed"]


def test_node_attempts_count_per_node_execution(tmp_path: Path) -> None:
    with lab(tmp_path, w1_approval.build) as w1:
        w1.run(*w1_ops("approve"))
        starts = [
            (e.data["node"], e.attempt, e.op_id) for e in w1.events() if e.kind == "node.start"
        ]
        assert starts == [
            ("plan", 1, "start-1"),
            ("approval", 1, "start-1"),
            ("approval", 2, "resume-1"),  # LangGraph re-runs the interrupted node
            ("execute", 1, "resume-1"),
            ("record", 1, "resume-1"),
        ]


def turn(text: str, message_id: str) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": text, "id": message_id}]}


def test_w5_two_turns_in_sequence_form_one_chain(tmp_path: Path) -> None:
    with lab(tmp_path, w5_continuation.build) as w5:
        first, second = w5.run(
            ("turn-1", "t1", turn("hi", "human-1")),
            ("turn-2", "t1", turn("and again", "human-2")),
        )
        messages = summary(w5_continuation.summarize(second))["messages"]
        assert [m[0] for m in messages] == ["human", "ai", "human", "ai"]
        assert [m[1] for m in messages] == [
            "human-1",
            "ai:t1:respond:0:1",
            "human-2",
            "ai:t1:respond:1:1",
        ]
        assert summary(w5_continuation.summarize(first))["messages"][-1][1] == "ai:t1:respond:0:1"
        # A single chain: each checkpoint's parent is the one written before it.
        tuples = list(w5.saver.list({"configurable": {"thread_id": "t1"}}))
        ids = [t.config.get("configurable", {}).get("checkpoint_id") for t in reversed(tuples)]
        parents = [
            (t.parent_config or {}).get("configurable", {}).get("checkpoint_id")
            for t in reversed(tuples)
        ]
        assert parents == [None, *ids[:-1]]
        assert w5.ledger.rows() == []


def normalized(lab_: Lab, thread: str) -> tuple[Any, ...]:
    state = lab_.state(thread)
    ledger = [
        (r["effect_key"], r["attempt"], r["node"], r["op_id"], r["payload_hash"])
        for r in lab_.ledger.rows()
    ]
    return repr(state).replace(thread, "T"), repr(ledger).replace(thread, "T")


def test_w1_is_deterministic_across_runs(tmp_path: Path) -> None:
    outcomes = set()
    for n in range(3):
        directory = tmp_path / str(n)
        directory.mkdir()
        with lab(directory, w1_approval.build_misuse) as w1:
            w1.run(*w1_ops("approve", thread=f"b/c/{n}/t1"))
            outcomes.add(normalized(w1, f"b/c/{n}/t1"))
    assert len(outcomes) == 1


class RefusingSink(LedgerSink):
    """Counts attempts, but refuses every effect as the sink would with a 4xx."""

    def post_effect(self, trial: TrialId, effect: EffectRequest) -> dict[str, Any]:
        from crashcourt.sink.client import SinkError

        raise SinkError("POST /effects: HTTP 409 another trial is open", 409)


def test_a_refused_effect_is_recorded_and_never_swallowed(tmp_path: Path) -> None:
    history = HistoryWriter(tmp_path / "w1.jsonl", run=RUN, proc="w1")
    ledger = Ledger(tmp_path / "ledger.sqlite", TRIAL)
    saver = InMemorySaver()
    graph = w1_approval.build(wrap(saver, history=history), Harness(history, RefusingSink(ledger)))
    w1 = Lab(graph, history, ledger, saver)
    try:
        w1.run(w1_ops("approve")[0])
        with pytest.raises(Exception, match="HTTP 409"):
            w1.run(w1_ops("approve")[1])
        events = w1.events()
        [response] = [e for e in events if e.kind == "effect.response"]
        assert response.status == "fail" and "409" in str(response.error)
        [end] = [e for e in events if e.kind == "node.end" and e.data["node"] == "execute"]
        assert end.status == "fail" and end.data["outcome"] == "raised"
        assert w1.ledger.rows() == []
    finally:
        ledger.close()
        history.close()
