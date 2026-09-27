"""Armed failpoints: where they fire, what they record, and what a pause blocks."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import pytest
from langgraph.checkpoint.base import Checkpoint, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from crashcourt.failpoints.barrier import InProcessBarrier
from crashcourt.failpoints.context import operation
from crashcourt.failpoints.registry import Arm, FailpointRegistry
from crashcourt.failpoints.saver import FailpointError, FailpointSaver, wrap
from crashcourt.history.merge import merge
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.spec.events import Event

RUN = RunIdentity(batch="DEMO-fp", cell="DEMO-cell", trial=0, seed="s")


def config(thread: str = "t1", checkpoint_id: str | None = None) -> Any:
    configurable: dict[str, Any] = {"thread_id": thread, "checkpoint_ns": ""}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def checkpoint() -> Checkpoint:
    cp = empty_checkpoint()
    cp["channel_values"] = {"n": 1}
    cp["channel_versions"] = {"n": "1"}
    return cp


class Rig:
    def __init__(self, tmp_path: Path, worker: str = "worker-1") -> None:
        self.worker_log = tmp_path / f"{worker}.jsonl"
        self.orch_log = tmp_path / "orchestrator.jsonl"
        self.history = HistoryWriter(self.worker_log, run=RUN, proc=worker)
        self.orchestrator = HistoryWriter(self.orch_log, run=RUN, proc="orchestrator")
        self.registry = FailpointRegistry(worker)
        self.barrier = InProcessBarrier(self.orchestrator)
        self.inner = InMemorySaver()
        self.saver = wrap(
            self.inner, history=self.history, registry=self.registry, barrier=self.barrier
        )

    def events(self) -> tuple[Event, ...]:
        return read_history(self.worker_log).events

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events()]

    def close(self) -> None:
        self.history.close()
        self.orchestrator.close()


@pytest.fixture
def rig(tmp_path: Path) -> Any:
    r = Rig(tmp_path)
    yield r
    r.close()


def test_disarmed_calls_record_call_and_return(rig: Rig) -> None:
    cp = checkpoint()
    with operation("op-1"):
        saved = rig.saver.put(config(), cp, {"source": "input", "step": -1}, {"n": "1"})
        rig.saver.put_writes(saved, [("n", 2)], "task-1")
        assert rig.saver.get_tuple(config()) is not None
    events = rig.events()
    assert [e.kind for e in events] == [
        "saver.put.call",
        "saver.put.return",
        "saver.put_writes.call",
        "saver.put_writes.return",
        "saver.get_tuple.call",
        "saver.get_tuple.return",
    ]
    assert {e.op_id for e in events} == {"op-1"}
    assert events[0].checkpoint_id == cp["id"] and events[0].parent_checkpoint_id is None
    assert events[3].task_id == "task-1" and events[2].data["channels"] == ["n"]
    assert events[5].checkpoint_id == cp["id"] and events[5].data["pending_writes"] == 1


def test_inner_exceptions_are_recorded_and_reraised(rig: Rig) -> None:
    with pytest.raises(KeyError):
        rig.saver.put_writes(config(), [("n", 1)], "task-1")  # no checkpoint_id in config
    assert rig.kinds() == ["saver.put_writes.call", "saver.put_writes.raise"]
    assert rig.events()[-1].status == "fail" and "KeyError" in (rig.events()[-1].error or "")


def _put_in_thread(saver: FailpointSaver, cp: Checkpoint) -> threading.Thread:
    thread = threading.Thread(
        target=lambda: saver.put(config(), cp, {"source": "loop", "step": 0}, {"n": "1"}),
        daemon=True,
    )
    thread.start()
    return thread


@pytest.mark.parametrize(
    ("point", "stored_while_paused"), [("put.before", False), ("put.after", True)]
)
def test_pause_blocks_exactly_at_its_point(rig: Rig, point: str, stored_while_paused: bool) -> None:
    rig.registry.arm(Arm(point, "pause"))  # type: ignore[arg-type]
    cp = checkpoint()
    thread = _put_in_thread(rig.saver, cp)
    proc, hit_seq = rig.barrier.wait_for_hit()
    assert thread.is_alive()
    assert (rig.inner.get_tuple(config()) is not None) is stored_while_paused
    rig.barrier.release(proc, hit_seq)
    thread.join(5)
    assert not thread.is_alive()
    assert rig.inner.get_tuple(config()) is not None
    kinds = rig.kinds()
    assert kinds[kinds.index("fp.hit") + 1] == "fp.action"
    hit = next(e for e in rig.events() if e.kind == "fp.hit")
    assert hit.fp == point and hit.data == {"action": "pause", "occurrence": 1}


def test_pause_events_merge_in_causal_order(rig: Rig) -> None:
    rig.registry.arm(Arm("put.before", "pause"))
    thread = _put_in_thread(rig.saver, checkpoint())
    rig.barrier.release(*rig.barrier.wait_for_hit())
    thread.join(5)
    rig.close()
    merged = merge([read_history(rig.worker_log).events, read_history(rig.orch_log).events])
    order = [e.kind for e in merged if e.kind.startswith("fp.")]
    assert order == ["fp.hit", "fp.release", "fp.action"]


def test_matching_by_worker_operation_and_occurrence(rig: Rig) -> None:
    rig.registry.arm(Arm("get_tuple.after", "none", worker="worker-2"))
    rig.registry.arm(Arm("get_tuple.after", "none", op_id="op-b", occurrence=2))
    for op in ("op-a", "op-b", "op-a", "op-b", "op-b"):
        with operation(op):
            rig.saver.get_tuple(config())
    hits = [e for e in rig.events() if e.kind == "fp.hit"]
    assert [(h.op_id, h.data["occurrence"]) for h in hits] == [("op-b", 2)]
    assert rig.registry.armed  # the worker-2 arm never matched and stays armed


def test_thread_filter(rig: Rig) -> None:
    rig.registry.arm(Arm("get_tuple.before", "none", thread_id="t2"))
    rig.saver.get_tuple(config("t1"))
    rig.saver.get_tuple(config("t2"))
    [hit] = [e for e in rig.events() if e.kind == "fp.hit"]
    assert hit.thread_id == "t2"


def test_list_records_completion_and_early_close(rig: Rig) -> None:
    for step in range(3):
        rig.inner.put(config(), checkpoint(), {"source": "loop", "step": step}, {"n": "1"})
    assert len(list(rig.saver.list(config()))) == 3
    iterator = cast(Generator[Any], rig.saver.list(config()))
    next(iterator)
    iterator.close()
    returns = [e.data for e in rig.events() if e.kind == "saver.list.return"]
    assert returns == [{"items": 3, "complete": True}, {"items": 1, "complete": False}]


def test_async_pause_parks_only_its_own_coroutine(rig: Rig) -> None:
    rig.registry.arm(Arm("put.before", "pause"))

    async def scenario() -> int:
        ticks = 0
        put = asyncio.create_task(
            rig.saver.aput(config(), checkpoint(), {"source": "loop", "step": 0}, {"n": "1"})
        )
        hit = await asyncio.to_thread(rig.barrier.wait_for_hit)
        for _ in range(20):  # the event loop keeps running while the put is parked
            ticks += 1
            await asyncio.sleep(0)
        assert not put.done()
        rig.barrier.release(*hit)
        await asyncio.wait_for(put, 5)
        return ticks

    assert asyncio.run(scenario()) == 20
    assert rig.inner.get_tuple(config()) is not None


def test_pause_without_a_barrier_is_a_spec_error(tmp_path: Path) -> None:
    with HistoryWriter(tmp_path / "w.jsonl", run=RUN, proc="w") as history:
        registry = FailpointRegistry("w")
        registry.arm(Arm("get_tuple.before", "pause"))
        saver = wrap(InMemorySaver(), history=history, registry=registry)
        with pytest.raises(FailpointError, match="no barrier"):
            saver.get_tuple(config())


def test_failpoints_need_a_history_writer() -> None:
    with pytest.raises(FailpointError, match="history"):
        wrap(InMemorySaver(), registry=FailpointRegistry("w"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"point": "put.during", "action": "none"},
        {"point": "put.before", "action": "explode"},
        {"point": "put.before", "action": "signal"},
        {"point": "put.before", "action": "none", "signal": "SIGKILL"},
        {"point": "put.before", "action": "none", "occurrence": 0},
    ],
)
def test_invalid_arms_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Arm(**kwargs)
