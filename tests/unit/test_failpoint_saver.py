"""Armed failpoints: where they fire, what they record, and what a pause blocks."""

from __future__ import annotations

import asyncio
import signal
import threading
from collections.abc import AsyncGenerator, Callable, Coroutine, Generator
from pathlib import Path
from typing import Any, cast

import pytest
from langgraph.checkpoint.base import Checkpoint, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from crashcourt.failpoints.barrier import InProcessBarrier
from crashcourt.failpoints.context import operation
from crashcourt.failpoints.registry import POINTS, Arm, FailpointRegistry
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


def _sync_call(saver: FailpointSaver, method: str, base: Any) -> Callable[[], object]:
    calls: dict[str, Callable[[], object]] = {
        "put": lambda: saver.put(config(), checkpoint(), {"source": "loop", "step": 1}, {"n": "2"}),
        "put_writes": lambda: saver.put_writes(base, [("n", 5)], "task-1"),
        "get_tuple": lambda: saver.get_tuple(config()),
        "list": lambda: list(saver.list(config())),
    }
    return calls[method]


def _async_call(saver: FailpointSaver, method: str, base: Any) -> Callable[[], object]:
    async def collect() -> object:
        return [item async for item in saver.alist(config())]

    calls: dict[str, Callable[[], Coroutine[Any, Any, object]]] = {
        "put": lambda: saver.aput(
            config(), checkpoint(), {"source": "loop", "step": 1}, {"n": "2"}
        ),
        "put_writes": lambda: saver.aput_writes(base, [("n", 5)], "task-1"),
        "get_tuple": lambda: saver.aget_tuple(config()),
        "list": collect,
    }
    call = calls[method]
    return lambda: asyncio.run(call())


def _stored(rig: Rig, method: str) -> bool:
    """Whether the paused call's write is already in the inner saver."""
    if method == "put":
        return len(list(rig.inner.list(config()))) == 2
    latest = rig.inner.get_tuple(config())
    assert latest is not None
    return any(task == "task-1" for task, _, _ in latest.pending_writes or ())


def _count_inner_calls(rig: Rig, name: str) -> list[int]:
    """Count calls the wrapper makes to the inner saver's `name`."""
    calls = [0]
    original = getattr(rig.inner, name)

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        return original(*args, **kwargs)

    setattr(rig.inner, name, counted)
    return calls


@pytest.mark.parametrize("flavor", ["sync", "async"])
@pytest.mark.parametrize("point", POINTS)
def test_pause_blocks_exactly_at_its_point(rig: Rig, point: str, flavor: str) -> None:
    base = rig.inner.put(config(), checkpoint(), {"source": "input", "step": -1}, {"n": "1"})
    method, position = point.split(".")
    call = (_sync_call if flavor == "sync" else _async_call)(rig.saver, method, base)
    inner_calls = _count_inner_calls(rig, method if flavor == "sync" else f"a{method}")
    rig.registry.arm(Arm(point, "pause"))  # type: ignore[arg-type]
    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    proc, hit_seq = rig.barrier.wait_for_hit()
    try:
        # A .before pause precedes the inner call; an .after pause follows it.
        assert inner_calls == [0 if position == "before" else 1]
        calls = [e.kind for e in rig.events() if e.kind.startswith("saver.")]
        # Paused after the call event and before the return event.
        assert calls == [f"saver.{method}.call"]
        assert thread.is_alive()
        if method in {"put", "put_writes"}:
            assert _stored(rig, method) is (position == "after")
    finally:
        # Always release: a thread left parked would keep the interpreter from exiting.
        rig.barrier.release(proc, hit_seq)
        thread.join(5)
    assert not thread.is_alive()
    kinds = rig.kinds()
    assert kinds[kinds.index("fp.hit") + 1] == "fp.action"
    assert kinds[-1] == f"saver.{method}.return"
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
    *_, hit, raised = read_history(tmp_path / "w.jsonl").events
    assert raised.kind == "saver.get_tuple.raise" and raised.status == "fail"
    assert (hit.kind, hit.fp, hit.status) == ("fp.hit", "get_tuple.before", "fail")
    assert hit.error is not None and "no barrier" in hit.error


def test_two_arms_due_on_one_call_are_a_spec_error(rig: Rig) -> None:
    rig.registry.arm(Arm("get_tuple.before", "none"))
    rig.registry.arm(Arm("get_tuple.before", "none", op_id="op-1"))
    with operation("op-1"), pytest.raises(FailpointError, match="2 arms fire on one"):
        rig.saver.get_tuple(config())
    *_, hit, raised = rig.events()
    assert (hit.kind, hit.status) == ("fp.hit", "fail")
    assert raised.kind == "saver.get_tuple.raise"
    assert not rig.registry.armed  # neither arm is left waiting for a call that will not come
    assert len(rig.registry.collided) == 2


def test_arms_due_on_different_calls_both_fire(rig: Rig) -> None:
    rig.registry.arm(Arm("get_tuple.before", "none", occurrence=1))
    rig.registry.arm(Arm("get_tuple.before", "none", occurrence=2))
    rig.saver.get_tuple(config())
    rig.saver.get_tuple(config())
    hits = [e.data["occurrence"] for e in rig.events() if e.kind == "fp.hit"]
    assert hits == [1, 2]


def test_list_closes_the_inner_iterator_on_early_exit(rig: Rig) -> None:
    closed: list[str] = []
    # Hold every inner iterator, so reference counting or asyncio's shutdown cannot close it;
    # only the wrapper's own close can.
    held: list[object] = []

    def sync_items(*args: Any, **kwargs: Any) -> Generator[Any]:
        try:
            yield from InMemorySaver.list(rig.inner, *args, **kwargs)
        finally:
            closed.append("sync")

    async def async_items(*args: Any, **kwargs: Any) -> AsyncGenerator[Any]:
        try:
            for item in InMemorySaver.list(rig.inner, *args, **kwargs):
                yield item
        finally:
            closed.append("async")

    def inner_list(*args: Any, **kwargs: Any) -> Generator[Any]:
        items = sync_items(*args, **kwargs)
        held.append(items)
        return items

    def inner_alist(*args: Any, **kwargs: Any) -> AsyncGenerator[Any]:
        items = async_items(*args, **kwargs)
        held.append(items)
        return items

    for step in range(3):
        rig.inner.put(config(), checkpoint(), {"source": "loop", "step": step}, {"n": "1"})
    rig.inner.list = inner_list  # type: ignore[method-assign]
    rig.inner.alist = inner_alist  # type: ignore[method-assign]
    iterator = cast(Generator[Any], rig.saver.list(config()))
    next(iterator)
    iterator.close()
    assert closed == ["sync"]

    async def first_then_close() -> None:
        items = cast(AsyncGenerator[Any], rig.saver.alist(config()))
        await anext(items)
        await items.aclose()
        assert closed == ["sync", "async"]  # before asyncio.run could close anything itself

    asyncio.run(first_then_close())
    assert len(held) == 2


def test_with_allowlist_keeps_the_wrapper_when_nothing_changes(rig: Rig) -> None:
    assert rig.saver.with_allowlist([]) is rig.saver


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
        {"point": "put.before", "action": "signal", "signal": "SIGFOO"},
        {"point": "put.before", "action": "signal", "signal": "SIGTERM"},  # Python may handle it
        {"point": "put.before", "action": "signal", "signal": "SIGINT"},
        {"point": "put.before", "action": "exit", "exit_code": 256},
        {"point": "put.before", "action": "none", "exit_code": 3},
    ],
)
def test_invalid_arms_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Arm(**kwargs)


def test_a_signal_the_host_lacks_fails_the_hit(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(signal, "SIGQUIT", raising=False)  # Windows has no SIGQUIT anyway
    rig.registry.arm(Arm("get_tuple.before", "signal", signal="SIGQUIT"))
    with pytest.raises(FailpointError, match="not available"):
        rig.saver.get_tuple(config())
    assert [(e.kind, e.status) for e in rig.events()][-2:] == [
        ("fp.hit", "fail"),
        ("saver.get_tuple.raise", "fail"),
    ]
    assert "fp.action" not in rig.kinds()


def test_a_failpoint_error_after_the_inner_call_is_recorded_as_a_raise(rig: Rig) -> None:
    rig.registry.arm(Arm("put.after", "none"))
    rig.registry.arm(Arm("put.after", "none", op_id="op-1"))
    with operation("op-1"), pytest.raises(FailpointError):
        rig.saver.put(config(), checkpoint(), {"source": "loop", "step": 0}, {"n": "1"})
    assert rig.kinds()[-2:] == ["fp.hit", "saver.put.raise"]
    assert rig.inner.get_tuple(config()) is not None  # the write itself went through
