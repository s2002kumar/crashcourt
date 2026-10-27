"""Worker pieces that need no process: the control barrier, the environment, and last_seq."""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.orchestrator import workers
from crashcourt.orchestrator.topology import Route
from crashcourt.orchestrator.workers import (
    LIBPQ_ADDRESS_VARIABLES,
    ProcessWorker,
    worker_environment,
)
from crashcourt.spec.events import Event
from crashcourt.spec.protocol import Hit, ProtocolError
from crashcourt.worker.main import DSN_ENV, ControlBarrier

RUN = RunIdentity("b", "c", 1, "s")


class RecordingLink:
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.arrived = asyncio.Event()

    async def send(self, message: object) -> None:
        self.sent.append(message)
        self.arrived.set()


def hit_event(tmp_path: Path) -> Event:
    with HistoryWriter(tmp_path / "w1.jsonl", run=RUN, proc="w1") as history:
        history.emit("saver.get_tuple.call", op_id="op-1")
        return history.emit(
            "fp.hit",
            op_id="op-1",
            fp="get_tuple.after",
            data={"action": "pause", "occurrence": 1},
        )


def test_a_parked_call_reports_its_hit_and_waits_for_its_release(tmp_path: Path) -> None:
    hit = hit_event(tmp_path)

    async def scenario() -> list[object]:
        loop = asyncio.get_running_loop()
        link = RecordingLink()
        barrier = ControlBarrier(loop, link)  # type: ignore[arg-type]
        released = threading.Event()

        def park() -> None:
            barrier.wait(hit)
            released.set()

        parked = loop.run_in_executor(None, park)
        await link.arrived.wait()
        assert barrier.parked == 1 and not released.is_set()
        barrier.release(hit.seq)
        await parked
        assert released.is_set() and barrier.parked == 0
        return link.sent

    sent = asyncio.run(scenario())
    assert sent == [Hit(hit_seq=1, fp="get_tuple.after", op_id="op-1", occurrence=1)]


def test_a_release_for_nothing_parked_is_a_protocol_error() -> None:
    async def scenario() -> None:
        barrier = ControlBarrier(asyncio.get_running_loop(), RecordingLink())  # type: ignore[arg-type]
        with pytest.raises(ProtocolError, match="not parked"):
            barrier.release(7)

    asyncio.run(scenario())


def test_the_barrier_refuses_to_park_the_event_loop_itself(tmp_path: Path) -> None:
    hit = hit_event(tmp_path)

    async def scenario() -> None:
        barrier = ControlBarrier(asyncio.get_running_loop(), RecordingLink())  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="event loop"):
            barrier.wait(hit)

    asyncio.run(scenario())


def test_the_worker_environment_has_no_other_way_to_the_database() -> None:
    parent = {"PATH": "/bin", "PGHOSTADDR": "10.0.0.1", "PGSERVICE": "prod", "PGUSER": "u"}
    env = worker_environment(parent, "host=127.0.0.1 port=15433")
    assert env == {"PATH": "/bin", "PGUSER": "u", DSN_ENV: "host=127.0.0.1 port=15433"}
    assert "PGHOSTADDR" in LIBPQ_ADDRESS_VARIABLES


def test_last_seq_names_the_last_event_written(tmp_path: Path) -> None:
    with HistoryWriter(tmp_path / "h.jsonl", run=RUN, proc="w1") as history:
        assert history.last_seq is None
        history.emit("saver.put.call")
        history.emit("saver.put.return", status="ok")
        assert history.last_seq == 1


def test_the_worker_runs_the_pinned_saver_unless_a_labeled_mutant_is_named() -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from crashcourt.worker.main import _saver_class  # pyright: ignore[reportPrivateUsage]
    from tests.mutants.savers import M3NoParent

    assert _saver_class(None) is AsyncPostgresSaver
    assert _saver_class("M3") is M3NoParent


@pytest.mark.skipif(sys.platform == "win32", reason="worker control sockets require POSIX")
def test_failed_worker_spawn_closes_log_and_unlinks_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process creation error must not leak a listening socket or an open log."""
    sockets = tmp_path / "sockets"
    sockets.mkdir()
    observed: dict[str, Any] = {}

    def fail_spawn(*_args: Any, **kwargs: Any) -> Any:
        observed["log"] = kwargs["stdout"]
        raise OSError("injected spawn failure")

    monkeypatch.setattr(workers.subprocess, "Popen", fail_spawn)
    route = Route("w1", "postgres-w1", "127.0.0.1:15433", "postgres:5432", 15433, "dsn")
    with (
        HistoryWriter(tmp_path / "orchestrator.jsonl", run=RUN, proc="orchestrator") as history,
        pytest.raises(OSError, match="injected spawn failure"),
    ):
        ProcessWorker(
            name="w1",
            route=route,
            run=RUN,
            workload="w1",
            sink_url="http://127.0.0.1:8480",
            trial_dir=tmp_path,
            socket_dir=sockets,
            history=history,
            notes=asyncio.Queue(),
            cwd=tmp_path,
        )
    assert observed["log"].closed
    assert not (sockets / "w1.sock").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="worker control sockets require POSIX")
def test_failed_worker_log_open_closes_the_control_socket(tmp_path: Path) -> None:
    sockets = tmp_path / "sockets"
    sockets.mkdir()
    route = Route("w1", "postgres-w1", "127.0.0.1:15433", "postgres:5432", 15433, "dsn")
    with (
        HistoryWriter(tmp_path / "orchestrator.jsonl", run=RUN, proc="orchestrator") as history,
        pytest.raises(FileNotFoundError),
    ):
        ProcessWorker(
            name="w1",
            route=route,
            run=RUN,
            workload="w1",
            sink_url="http://127.0.0.1:8480",
            trial_dir=tmp_path / "missing-directory",
            socket_dir=sockets,
            history=history,
            notes=asyncio.Queue(),
            cwd=tmp_path,
        )
    assert not (sockets / "w1.sock").exists()
