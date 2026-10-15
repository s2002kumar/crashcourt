"""Worker pieces that need no process: the control barrier, the environment, and last_seq."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.orchestrator.workers import LIBPQ_ADDRESS_VARIABLES, worker_environment
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
