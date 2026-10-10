"""How a worker reports a run: a result for the run's own failures, a harness error for its own."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from crashcourt.failpoints.context import current_op
from crashcourt.history.writer import RunIdentity
from crashcourt.spec.protocol import Closed, Result, Run, Shutdown, encode, read_line
from crashcourt.worker.main import HarnessError, WorkerHistory, execute

RUN = Run(op_id="op-1", thread_id="b/c/1/t1", durability="sync", input={"n": 1})


class Graph:
    def __init__(self, history: WorkerHistory, outcome: object) -> None:
        self.history = history
        self.outcome = outcome
        self.calls: list[tuple[Any, ...]] = []

    async def ainvoke(self, payload: Any, config: Any, *, durability: str) -> Any:
        self.calls.append((payload, config, durability, current_op()))
        self.history.emit("saver.put.call", op_id=current_op())
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def writer(tmp_path: Path) -> WorkerHistory:
    return WorkerHistory(tmp_path / "w1.jsonl", run=RunIdentity("b", "c", 1, "s"), proc="w1")


def test_a_completed_run_is_ok_with_its_summary_and_last_event(tmp_path: Path) -> None:
    with writer(tmp_path) as history:
        graph = Graph(history, {"trace": ["first"]})
        result = asyncio.run(execute(graph, lambda out: {"n": len(out["trace"])}, history, RUN))
    assert result == Result(op_id="op-1", status="ok", output={"n": 1}, worker_seq=0)
    # The run sees its op, its thread, and the scenario's durability.
    assert graph.calls == [({"n": 1}, {"configurable": {"thread_id": "b/c/1/t1"}}, "sync", "op-1")]


def test_an_error_from_the_run_is_a_failed_result(tmp_path: Path) -> None:
    with writer(tmp_path) as history:
        graph = Graph(history, ConnectionResetError("server closed the connection"))
        result = asyncio.run(execute(graph, lambda out: out, history, RUN))
    assert result.status == "fail" and result.worker_seq == 0
    assert result.error == "ConnectionResetError: server closed the connection"
    assert result.error_type == "builtins.ConnectionResetError"


@pytest.mark.parametrize("summarize", [lambda out: 1 / 0, lambda out: {"x": object()}])
def test_a_run_that_completed_but_cannot_be_reported_is_a_harness_error(
    tmp_path: Path, summarize: Any
) -> None:
    # Never a failed result: the run itself succeeded.
    with writer(tmp_path) as history:
        graph = Graph(history, {"trace": []})
        with pytest.raises(HarnessError, match="op-1 completed"):
            asyncio.run(execute(graph, summarize, history, RUN))


class BadEventGraph(Graph):
    """A run during which the saver's history write fails, as a harness defect would."""

    async def ainvoke(self, payload: Any, config: Any, *, durability: str) -> Any:
        # fp.hit needs fp, so the write is refused. LangGraph may swallow or wrap such an
        # error; the writer still remembers it.
        with contextlib.suppress(ValueError):
            self.history.emit("fp.hit", op_id=current_op())
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize("outcome", [{"trace": []}, RuntimeError("wrapped by the target")])
def test_a_failed_history_write_during_a_run_is_a_harness_error(
    tmp_path: Path, outcome: object
) -> None:
    # Neither a failed result nor an ok one: the history no longer holds the whole run.
    with writer(tmp_path) as history:
        graph = BadEventGraph(history, outcome)
        with pytest.raises(HarnessError, match=r"writing fp\.hit failed"):
            asyncio.run(execute(graph, lambda out: out, history, RUN))


@pytest.mark.skipif(sys.platform != "linux", reason="AF_UNIX socket pairs; CI runs it")
def test_a_failed_send_keeps_what_the_dead_peer_sent() -> None:
    from crashcourt.orchestrator.workers import Channel

    async def scenario() -> list[object]:
        ours, theirs = socket.socketpair()  # AF_UNIX, SOCK_STREAM on Linux
        channel = await Channel.open(ours)
        theirs.sendall(encode(Result(op_id="op-1", status="ok")))
        theirs.close()  # the worker dies right after sending its result
        got: list[object] = []
        with pytest.raises(OSError):
            await channel.send(Shutdown())  # the peer is gone: EPIPE
        got.append(await read_line(channel.reader))
        with pytest.raises(Closed):
            await read_line(channel.reader)
        channel.close()
        return got

    assert asyncio.run(scenario()) == [encode(Result(op_id="op-1", status="ok"))[:-1]]
