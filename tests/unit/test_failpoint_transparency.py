"""Control C4 in the unit tier: a disarmed FailpointSaver is indistinguishable from its inner saver.

The official conformance suite (langgraph-checkpoint-conformance 0.0.2) must report identical
results for the raw and the wrapped saver; the suite calls only async methods, so a fixed sync
script must return the same through both; and a graph run through either must persist the same
states. The wrapped saver always records, with an empty failpoint registry, as in a trial, and
what it stored is read back through the raw saver, not through the wrapper. Postgres is covered
in tests/integration.
"""

from __future__ import annotations

import asyncio
import operator
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Annotated, Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.conformance import checkpointer_test, validate
from langgraph.checkpoint.conformance.capabilities import Capability, DetectedCapabilities
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from crashcourt.failpoints.saver import FailpointSaver, wrap
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity

from ..c4 import assert_meaningful, recorded, script_checkpoints, sync_reads, sync_script, writer

RUN = RunIdentity(batch="DEMO-c4", cell="DEMO-cell", trial=0, seed="s")
Factory = Callable[[], AbstractAsyncContextManager[BaseCheckpointSaver[Any]]]


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


async def conformance(
    name: str, factory: Callable[[], AsyncIterator[BaseCheckpointSaver[Any]]]
) -> dict[str, Any]:
    registered = checkpointer_test(name=name)(factory)
    report = await validate(registered)
    result = report.to_dict()
    result.pop("checkpointer_name", None)
    return result


def test_in_memory_saver_conformance_is_identical_through_the_wrapper(tmp_path: Path) -> None:
    history = writer(tmp_path / "worker.jsonl", "memory")

    async def raw() -> AsyncIterator[BaseCheckpointSaver[Any]]:
        yield InMemorySaver()

    async def wrapped() -> AsyncIterator[BaseCheckpointSaver[Any]]:
        yield recorded(InMemorySaver(), history)

    with history:
        raw_report = run(conformance("raw", raw))
        wrapped_report = run(conformance("wrapped", wrapped))
    assert wrapped_report == raw_report
    assert read_history(tmp_path / "worker.jsonl").events, "the wrapper recorded saver calls"


def test_async_sqlite_saver_conformance_is_identical_through_the_wrapper(tmp_path: Path) -> None:
    history = writer(tmp_path / "worker.jsonl", "sqlite")

    async def raw() -> AsyncIterator[BaseCheckpointSaver[Any]]:
        async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
            yield saver

    async def wrapped() -> AsyncIterator[BaseCheckpointSaver[Any]]:
        async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
            yield recorded(saver, history)

    with history:
        assert run(conformance("wrapped", wrapped)) == run(conformance("raw", raw))
    assert read_history(tmp_path / "worker.jsonl").events, "the wrapper recorded saver calls"


def test_sync_calls_return_and_store_the_same_through_the_wrapper(tmp_path: Path) -> None:
    checkpoints = script_checkpoints()
    with (
        SqliteSaver.from_conn_string(str(tmp_path / "raw.sqlite")) as raw,
        SqliteSaver.from_conn_string(str(tmp_path / "wrapped.sqlite")) as inner,
        writer(tmp_path / "worker.jsonl", "sync") as history,
    ):
        through_raw = sync_script(raw, checkpoints)
        assert_meaningful(through_raw)
        assert sync_script(recorded(inner, history), checkpoints) == through_raw
        # What the wrapper stored, read without it.
        assert sync_reads(inner, through_raw[1]) == through_raw[-1]
    memory_raw, memory_inner = InMemorySaver(), InMemorySaver()
    with writer(tmp_path / "memory.jsonl", "sync-memory") as history:
        through_raw = sync_script(memory_raw, checkpoints)
        assert_meaningful(through_raw)
        assert sync_script(recorded(memory_inner, history), checkpoints) == through_raw
        assert sync_reads(memory_inner, through_raw[1]) == through_raw[-1]
    kinds = {e.kind for e in read_history(tmp_path / "worker.jsonl").events}
    assert {f"saver.{m}.return" for m in ("put", "put_writes", "get_tuple", "list")} <= kinds


class PruningSaver(InMemorySaver):
    async def aprune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        return None


def test_wrapper_offers_exactly_the_inner_capabilities() -> None:
    for inner in (InMemorySaver(), PruningSaver()):
        assert (
            DetectedCapabilities.from_instance(wrap(inner)).detected
            == DetectedCapabilities.from_instance(inner).detected
        )
    assert Capability.PRUNE in DetectedCapabilities.from_instance(wrap(PruningSaver())).detected
    assert type(wrap(InMemorySaver())) is FailpointSaver


class CountingVersions(InMemorySaver):
    def get_next_version(self, current: str | None, channel: None) -> str:
        return f"v{int((current or 'v0')[1:]) + 1}"


def test_versions_and_serializer_come_from_the_inner_saver() -> None:
    inner = CountingVersions()
    wrapped = wrap(inner)
    assert wrapped.serde is inner.serde
    assert wrapped.get_next_version("v4", None) == "v5"


class State(TypedDict):
    items: Annotated[list[str], operator.add]
    count: int


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> Any:
    def first(state: State) -> dict[str, Any]:
        return {"items": ["a"], "count": state["count"] + 1}

    def second(state: State) -> dict[str, Any]:
        return {"items": ["b"], "count": state["count"] + 1}

    graph = StateGraph(State)
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    return graph.compile(checkpointer=checkpointer)


def history_view(graph: Any, thread: str) -> list[tuple[Any, ...]]:
    config = {"configurable": {"thread_id": thread}}
    return [
        (s.values, s.next, s.metadata.get("step"), s.metadata.get("source"))
        for s in graph.get_state_history(config)
    ]


def test_a_graph_persists_the_same_states_through_the_wrapper(tmp_path: Path) -> None:
    with (
        SqliteSaver.from_conn_string(str(tmp_path / "raw.sqlite")) as raw_saver,
        SqliteSaver.from_conn_string(str(tmp_path / "wrapped.sqlite")) as inner,
        writer(tmp_path / "worker.jsonl", "graph") as history,
    ):
        wrapped_saver = recorded(inner, history)
        for durability in ("sync", "async", "exit"):
            thread = {"configurable": {"thread_id": durability}}
            raw_graph, wrapped_graph = build_graph(raw_saver), build_graph(wrapped_saver)
            raw_out = raw_graph.invoke({"items": [], "count": 0}, thread, durability=durability)
            wrapped_out = wrapped_graph.invoke(
                {"items": [], "count": 0}, thread, durability=durability
            )
            assert wrapped_out == raw_out
            # Read what the wrapper stored through the raw saver, not through the wrapper.
            stored = history_view(build_graph(inner), durability)
            assert stored == history_view(raw_graph, durability)
    kinds = {e.kind for e in read_history(tmp_path / "worker.jsonl").events}
    assert {"saver.put.call", "saver.put.return", "saver.get_tuple.call"} <= kinds


def test_an_async_graph_persists_the_same_states_through_the_wrapper(tmp_path: Path) -> None:
    raw_saver, inner = InMemorySaver(), InMemorySaver()

    async def scenario(history: HistoryWriter) -> tuple[Any, Any]:
        raw_graph, wrapped_graph = build_graph(raw_saver), build_graph(recorded(inner, history))
        thread = {"configurable": {"thread_id": "t1"}}
        raw_out = await raw_graph.ainvoke({"items": [], "count": 0}, thread, durability="sync")
        wrapped_out = await wrapped_graph.ainvoke(
            {"items": [], "count": 0}, thread, durability="sync"
        )
        return raw_out, wrapped_out

    with writer(tmp_path / "worker.jsonl", "async-graph") as history:
        raw_out, wrapped_out = run(scenario(history))
    assert wrapped_out == raw_out
    # Read what the wrapper stored through the raw saver, not through the wrapper.
    assert history_view(build_graph(inner), "t1") == history_view(build_graph(raw_saver), "t1")
