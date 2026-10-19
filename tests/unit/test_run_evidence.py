"""The post-trial evidence a trial leaves: when each read happens, what each file holds, that a
failed read leaves its file absent, and that nothing read for them changes what the trial does.

Workers here are in-process fakes standing in for ProcessWorker, so the trial runs without
processes, a database, or a sink; the database reads are stubs.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from crashcourt.checker.artifacts import (
    LedgerExport,
    SinkHealth,
    Snapshot,
    SnapshotMeta,
    SnapshotPair,
)
from crashcourt.experiment.observe import mismatch
from crashcourt.experiment.preregistration import LedgerRows
from crashcourt.experiment.verify import TrialFiles
from crashcourt.faults.toxiproxy import Proxy, Toxic
from crashcourt.history import writer
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter
from crashcourt.orchestrator import evidence, runner, topology
from crashcourt.orchestrator.dispatcher import Disconnected, Exited, Note, Received
from crashcourt.sink.client import SinkError
from crashcourt.sink.ledger import LEDGER_VERSION, TrialId
from crashcourt.snapshot import dump
from crashcourt.spec.protocol import Result, Run, Shutdown
from crashcourt.spec.scenario import parse_scenario

SOURCE = "c" * 64
DIRECT = "host=127.0.0.1 port=5432 user=crashcourt password=secret dbname=crashcourt"
SCENARIO = parse_scenario(
    {
        "v": 1,
        "name": "evidence",
        "cell": "cell",
        "seed": "seed",
        "workload": "smoke",
        "target": {"saver": "postgres", "driver": "async", "durability": "sync"},
        "workers": ["w1", "w2"],
        "ops": [
            {"id": "op-1", "worker": "w1", "thread": "t1", "after": "start"},
            {"id": "op-2", "worker": "w2", "thread": "t2", "after": "op-1"},
        ],
        "timeouts": {"run_s": 5, "hello_s": 5, "shutdown_s": 5},
    }
)
THREADS = ["b-ev/cell/1/t1", "b-ev/cell/1/t2"]


class World:
    """Everything outside the orchestrator, faked, with one log of who was asked what, when."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.opened: TrialId | None = None
        self.health_answers: list[dict[str, Any] | Exception] = []
        self.rows: list[dict[str, Any]] = []
        self.verify: dict[str, Any] | None = None
        self.ledger_error: Exception | None = None
        self.pretrial: dict[str, int] | Exception | None = None
        self.pair_error: Exception | None = None
        self.pair_args: dict[str, Any] = {}
        self.toxics: tuple[Proxy, ...] = ()
        self.reaped = True
        self.sent: list[str] = []

    # -- the sink
    def open_trial(self, trial: TrialId, *, apply_once: bool = False) -> dict[str, Any]:
        self.log.append("open_trial")
        self.opened = trial
        self.apply_once = apply_once
        return {"trial": vars(trial), "rows": 0}

    def health(self) -> dict[str, Any]:
        self.log.append("health")
        if self.health_answers:
            answer = self.health_answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return healthy(self.opened)

    def ledger(self, trial: TrialId) -> list[dict[str, Any]]:
        self.log.append("ledger")
        if self.ledger_error is not None:
            raise self.ledger_error
        return self.rows

    def verify_chain(self, trial: TrialId) -> dict[str, Any]:
        self.log.append("verify")
        return self.verify or {"ok": True, "rows": len(self.rows), "first_bad": None}

    # -- Toxiproxy
    def proxy(self, name: str) -> Proxy:
        return Proxy(name, "0.0.0.0:1", "postgres:5432", True, ())

    def proxies(self) -> tuple[Proxy, ...]:
        return self.toxics

    # -- the database (stubs of crashcourt.snapshot.dump)
    def pretrial_rows(self, dsn: str, threads: list[str]) -> dict[str, int]:
        self.log.append("pretrial")
        if isinstance(self.pretrial, Exception):
            raise self.pretrial
        return self.pretrial if self.pretrial is not None else dict.fromkeys(threads, 0)

    def take_pair(self, dsn: str, threads: list[str], pretrial: dict[str, int], **kw: Any) -> Any:
        self.log.append("take_pair")
        self.pair_args = {"dsn": dsn, "threads": threads, "pretrial": pretrial, **kw}
        if self.pair_error is not None:
            raise self.pair_error
        meta = SnapshotMeta(
            after_orchestrator_seq=kw["after_orchestrator_seq"],
            taken_ns=1,
            dsn=dump.redact(dsn),
            settings={},
            postmaster_start_ns=0,
            toxics_installed=tuple(kw["toxics_installed"]),
            threads=tuple(threads),
        )
        second = Snapshot(meta=meta.model_copy(update={"taken_ns": 2_000_000_001}))
        return SnapshotPair(first=Snapshot(meta=meta), second=second, pretrial_rows=pretrial)


def healthy(opened: TrialId | None) -> dict[str, Any]:
    return {
        "status": "ok",
        "source_sha256": SOURCE,
        "pid": 77,
        "ledger_version": LEDGER_VERSION,
        "trial": None if opened is None else vars(opened),
        "resume_error": None,
        "test_hooks": False,
    }


class FakeLink:
    """A worker that answers every run with ok and exits when asked to shut down."""

    world: World

    def __init__(self, *, name: str, notes: asyncio.Queue[Note], history: HistoryWriter, **_: Any):
        self.name = name
        self.pid = 1000 + int(name[1:])
        self.notes = notes
        self.history = history
        self.alive = True
        history.emit("worker.start", data={"proc": name, "pid": self.pid})

    async def hello(self, timeout: float) -> None:
        return None

    async def send(self, message: BaseModel) -> None:
        if not self.alive:
            raise ConnectionError("gone")
        self.world.sent.append(f"{self.name} {message.model_dump_json()}")
        if isinstance(message, Run):
            self.world.log.append(f"run {message.op_id}")
            result = Result(op_id=message.op_id, status="ok", output={"n": 1}, worker_seq=5)
            self.notes.put_nowait(Received(self.name, result))
        elif isinstance(message, Shutdown):
            self.alive = False
            self.notes.put_nowait(Disconnected(self.name))
            self.notes.put_nowait(Exited(self.name, 0))

    async def kill(self, fault_id: str) -> None:
        raise AssertionError("this scenario kills nothing")

    def force_kill(self) -> None:
        self.alive = False

    async def close(self) -> None:
        return None

    @property
    def reaped(self) -> bool:
        return self.world.reaped


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> World:
    w = World()
    link = type("Link", (FakeLink,), {"world": w})
    sockets = tmp_path / "sockets"
    sockets.mkdir()
    monkeypatch.setattr(runner, "ProcessWorker", link)
    monkeypatch.setattr(runner, "socket_directory", lambda: sockets)
    monkeypatch.setattr(
        runner, "rows_for_threads", lambda dsn, threads: dict.fromkeys(runner.THREAD_TABLES, 0)
    )
    monkeypatch.setattr(dump, "pretrial_rows", w.pretrial_rows)
    monkeypatch.setattr(dump, "take_pair", w.take_pair)
    return w


class SinkFace:
    """The SinkClient surface the runner uses, answered by the world."""

    def __init__(self, world: World) -> None:
        self.world = world

    def open_trial(self, trial: TrialId, *, apply_once: bool = False) -> dict[str, Any]:
        return self.world.open_trial(trial, apply_once=apply_once)

    def health(self) -> dict[str, Any]:
        return self.world.health()

    def ledger(self, trial: TrialId) -> list[dict[str, Any]]:
        return self.world.ledger(trial)

    def verify(self, trial: TrialId) -> dict[str, Any]:
        return self.world.verify_chain(trial)


def trial(world: World, out: Path) -> tuple[runner.TrialResult, Path]:
    settings = runner.Settings(
        root=out, direct_dsn=DIRECT, toxiproxy_url="", sink_url="", worker_ports=(15433, 15434)
    )
    ctx = runner._Batch(  # pyright: ignore[reportPrivateUsage]
        scenario=SCENARIO,
        settings=settings,
        batch="b-ev",
        directory=out,
        routes=topology.routes(SCENARIO.workers, DIRECT, settings.worker_ports),
        sink=SinkFace(world),  # pyright: ignore[reportArgumentType]
        toxiproxy=world,  # pyright: ignore[reportArgumentType]
        sink_source=SOURCE,
    )
    result = asyncio.run(runner.run_trial(ctx, 1))
    return result, out / "cell" / "trial-00001"


def test_the_before_readings_come_after_the_sink_opens_the_trial_and_before_any_dispatch(
    world: World, tmp_path: Path
) -> None:
    trial(world, tmp_path / "out")
    log = world.log
    assert log.index("open_trial") < log.index("health") < log.index("run op-1")
    assert log.index("pretrial") < log.index("run op-1")
    # The "after" reading, the ledger, and the snapshot all come once every run is over.
    last_run = log.index("run op-2")
    assert log.index("health", log.index("health") + 1) > last_run
    assert last_run < log.index("ledger") < log.index("take_pair")


def test_sink_health_holds_the_reading_before_then_the_reading_after(
    world: World, tmp_path: Path
) -> None:
    _, directory = trial(world, tmp_path / "out")
    raw = json.loads((directory / evidence.HEALTH_FILE).read_text(encoding="utf-8"))
    readings = [SinkHealth.model_validate(r) for r in raw]
    assert [r.when for r in readings] == ["before", "after"]
    assert [r.open_trial for r in readings] == ["b-ev/cell/1", "b-ev/cell/1"]
    assert {r.source_sha256 for r in readings} == {SOURCE}
    assert not any(r.hooks_enabled for r in readings)


def test_the_ledger_export_is_the_sinks_rows_and_verdict_and_t13_reads_it(
    world: World, tmp_path: Path
) -> None:
    _, directory = trial(world, tmp_path / "out")
    export = LedgerExport.model_validate_json((directory / evidence.LEDGER_FILE).read_bytes())
    assert export == LedgerExport(rows=(), verify_ok=True)
    files = TrialFiles(trial=1, directory=directory, checker={}, result=None)
    assert mismatch(LedgerRows(source="ledger", effect="charge", rows=0), files) is None


def test_the_snapshot_reads_the_direct_dsn_for_every_thread_after_quiescence(
    world: World, tmp_path: Path
) -> None:
    world.toxics = (
        Proxy("postgres", "0.0.0.0:1", "postgres:5432", True, (Toxic("lag", "latency"),)),
        Proxy("postgres-w1", "0.0.0.0:1", "postgres:5432", True, ()),
    )
    _, directory = trial(world, tmp_path / "out")
    args = world.pair_args
    assert args["dsn"] == DIRECT and args["threads"] == THREADS
    assert args["pretrial"] == dict.fromkeys(THREADS, 0)
    assert args["toxics_installed"] == ["postgres/lag"]
    orchestrator = read_history(directory / "orchestrator.jsonl").events
    assert args["after_orchestrator_seq"] == orchestrator[-1].seq
    assert {e.kind for e in orchestrator if e.seq <= orchestrator[-1].seq} >= {"worker.exit"}
    pair = SnapshotPair.model_validate_json((directory / evidence.SNAPSHOT_FILE).read_bytes())
    assert pair.first.meta.dsn == dump.redact(DIRECT) and "secret" not in pair.first.meta.dsn


def test_a_worker_not_reaped_leaves_no_snapshot(world: World, tmp_path: Path) -> None:
    world.reaped = False
    result, directory = trial(world, tmp_path / "out")
    assert "take_pair" not in world.log
    assert not (directory / evidence.SNAPSHOT_FILE).exists()
    assert any("was not reaped" in r for r in result.reasons) and not result.valid


def no_partials(directory: Path) -> bool:
    return not list(directory.glob("*.partial"))


def test_an_unreachable_sink_leaves_no_ledger_and_no_health(world: World, tmp_path: Path) -> None:
    world.ledger_error = SinkError("sink unreachable")
    result, directory = trial(world, tmp_path / "out")
    assert not (directory / evidence.LEDGER_FILE).exists()
    assert not (directory / evidence.HEALTH_FILE).exists()
    assert (directory / evidence.SNAPSHOT_FILE).exists() and no_partials(directory)
    assert any(evidence.LEDGER_FILE in r for r in result.reasons) and not result.valid


def test_an_unreachable_database_leaves_no_snapshot_and_still_dispatches(
    world: World, tmp_path: Path
) -> None:
    world.pretrial = OSError("connection refused")
    result, directory = trial(world, tmp_path / "out")
    assert "run op-1" in world.log and "take_pair" not in world.log
    assert not (directory / evidence.SNAPSHOT_FILE).exists() and no_partials(directory)
    assert any("pre-trial row counts could not be read" in r for r in result.reasons)


def test_a_failed_snapshot_leaves_no_file(world: World, tmp_path: Path) -> None:
    world.pair_error = OSError("server closed the connection")
    result, directory = trial(world, tmp_path / "out")
    assert not (directory / evidence.SNAPSHOT_FILE).exists() and no_partials(directory)
    assert any("server closed the connection" in r for r in result.reasons)


def test_a_refused_health_reading_leaves_no_health_file_and_still_dispatches(
    world: World, tmp_path: Path
) -> None:
    world.health_answers = [{**healthy(None), "resume_error": "ledger file missing"}]
    result, directory = trial(world, tmp_path / "out")
    assert "run op-1" in world.log
    assert not (directory / evidence.HEALTH_FILE).exists() and no_partials(directory)
    assert any("resume error" in r for r in result.reasons)


def test_a_ledger_and_verify_that_disagree_leave_no_ledger(world: World, tmp_path: Path) -> None:
    world.verify = {"ok": True, "rows": 1, "first_bad": None}
    result, directory = trial(world, tmp_path / "out")
    assert not (directory / evidence.LEDGER_FILE).exists() and no_partials(directory)
    assert any("counted 1 rows, /ledger returned 0" in r for r in result.reasons)


@pytest.fixture
def restart_clocks(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Both history clocks pinned to counters; calling the result starts them over."""
    counters: dict[str, Iterator[int]] = {}

    def restart() -> None:
        counters["mono"] = itertools.count(1_000)
        counters["wall"] = itertools.count(2_000)

    restart()
    monkeypatch.setattr(writer, "host_monotonic_ns", lambda: next(counters["mono"]))
    monkeypatch.setattr(writer.time, "time_ns", lambda: next(counters["wall"]))
    return restart


def test_nothing_read_for_the_evidence_changes_the_trial(
    world: World, tmp_path: Path, restart_clocks: Callable[[], None]
) -> None:
    trial(world, tmp_path / "a")
    first_history = (tmp_path / "a" / "cell" / "trial-00001" / "orchestrator.jsonl").read_bytes()
    first_sent = list(world.sent)

    # Every evidence read answers differently, or fails.
    world.sent.clear()
    world.health_answers = [SinkError("down"), {**healthy(None), "status": "starting"}]
    world.pretrial = {t: 9 for t in THREADS}
    world.pair_error = OSError("no database")
    world.toxics = (Proxy("postgres", "0.0.0.0:1", "x:1", True, (Toxic("t", "timeout"),)),)
    restart_clocks()
    trial(world, tmp_path / "b")
    second_history = (tmp_path / "b" / "cell" / "trial-00001" / "orchestrator.jsonl").read_bytes()

    assert second_history == first_history
    assert world.sent == first_sent
    kinds = [
        e.kind
        for e in read_history(tmp_path / "a" / "cell" / "trial-00001" / "orchestrator.jsonl").events
    ]
    assert kinds.count("client.invoke") == 2 and kinds.count("worker.exit") == 2
