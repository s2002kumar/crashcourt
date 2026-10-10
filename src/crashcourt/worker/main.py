"""A worker process: runs graphs through the failpoint saver and reports over the control socket.

    python -m crashcourt.worker --name w1 --socket /tmp/cc-x/w1.sock --history w1.jsonl \\
        --batch b --cell c --trial 1 --seed s --workload smoke

The target database's DSN comes from CRASHCOURT_WORKER_DSN (the worker's own Toxiproxy route),
not the command line. The worker connects to the orchestrator's socket, opens its connection pool
with the saver's documented settings (autocommit, prepare_threshold=0, dict_row), and only then
says hello. Each run executes as its own task, so the worker keeps reading while a run is parked
at a barrier. Every run gets exactly one result.

Exit statuses: 0 after shutdown; 2 when the orchestrator broke the protocol; 3 when the worker
could not start; 4 when the control connection was lost; 5 when the worker's own code failed
(a run completed but its output could not be reported). Any other ending (a signal, an exit armed
at a failpoint) is the orchestrator's to explain.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn, cast

from crashcourt.failpoints.context import operation
from crashcourt.failpoints.registry import Arm, FailpointRegistry
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.spec.events import Event, EventKind
from crashcourt.spec.protocol import (
    MAX_LINE,
    ArmMessage,
    Closed,
    Hello,
    Hit,
    ProtocolError,
    ReleaseMessage,
    Result,
    Run,
    encode,
    parse_from_orchestrator,
    read_line,
)

EXIT_OK = 0
EXIT_PROTOCOL = 2
EXIT_STARTUP = 3
EXIT_ORPHANED = 4
EXIT_HARNESS = 5
#: Statuses that mean the harness, not the target, ended the worker.
HARNESS_EXITS: Mapping[int, str] = {
    EXIT_PROTOCOL: "the orchestrator broke the control protocol",
    EXIT_STARTUP: "the worker could not start",
    EXIT_ORPHANED: "the worker lost its control connection",
    EXIT_HARNESS: "the worker's own code failed",
}

DSN_ENV = "CRASHCOURT_WORKER_DSN"
#: Each scenario workload: its module, and the function there that builds its graph from a
#: checkpointer and the worker's harness. The module also has summarize(output).
WORKLOADS: Mapping[str, tuple[str, str]] = {
    "smoke": ("crashcourt.workloads.smoke", "build"),
    "w1": ("crashcourt.workloads.w1_approval", "build"),
    "w1-misuse": ("crashcourt.workloads.w1_approval", "build_misuse"),
    "w5": ("crashcourt.workloads.w5_continuation", "build"),
}


@dataclass(frozen=True)
class WorkerConfig:
    name: str
    socket: Path
    history: Path
    run: RunIdentity
    workload: str
    dsn: str
    sink_url: str
    #: How long to wait for the pool's first connections.
    connect_timeout: float = 30.0


def _fatal(status: int, message: str) -> NoReturn:
    print(f"worker: {message}", file=sys.stderr, flush=True)
    # A run may be parked on a barrier thread; a normal exit would wait for it forever.
    os._exit(status)


class Link:
    """The worker's end of the control socket. Sends are serialized: one line at a time."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._lock = asyncio.Lock()

    async def send(self, message: Hello | Hit | Result) -> None:
        async with self._lock:
            try:
                self._writer.write(encode(message))
                await self._writer.drain()
            except (ConnectionError, OSError) as exc:
                _fatal(EXIT_ORPHANED, f"control connection lost while sending: {exc}")


class ControlBarrier:
    """The failpoint saver's barrier: report the hit, then wait for the orchestrator's release.

    `wait` runs on a thread other than the event loop's (the saver parks async calls with
    `asyncio.to_thread`), and blocks only that thread.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, link: Link) -> None:
        """Construct on the event loop's thread."""
        self._loop = loop
        self._loop_thread = threading.get_ident()
        self._link = link
        self._lock = threading.Lock()
        self._gates: dict[int, threading.Event] = {}

    def wait(self, hit: Event) -> None:
        if threading.get_ident() == self._loop_thread:
            raise RuntimeError("a barrier must not park the event loop's own thread")
        occurrence = hit.data.get("occurrence")
        message = Hit(
            hit_seq=hit.seq,
            fp=cast(Any, hit.fp),
            op_id=hit.op_id,
            occurrence=occurrence if isinstance(occurrence, int) else 1,
        )
        gate = threading.Event()
        with self._lock:
            self._gates[hit.seq] = gate  # before the hit is reported, so a release finds it
        asyncio.run_coroutine_threadsafe(self._link.send(message), self._loop).result()
        gate.wait()

    def release(self, hit_seq: int) -> None:
        with self._lock:
            gate = self._gates.pop(hit_seq, None)
        if gate is None:
            raise ProtocolError(f"release for hit {hit_seq}, which is not parked here")
        gate.set()

    @property
    def parked(self) -> int:
        with self._lock:
            return len(self._gates)


class HarnessError(Exception):
    """The worker's own code failed; nothing it could report would be trustworthy."""


class WorkerHistory(HistoryWriter):
    """The worker's history writer. It remembers a write that failed: the saver's events are
    written inside the run, so such a failure would otherwise surface as the run's error."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.failure: str | None = None

    def emit(self, kind: EventKind, **fields: Any) -> Event:
        try:
            return super().emit(kind, **fields)
        except Exception as exc:
            if self.failure is None:
                self.failure = f"writing {kind} failed: {type(exc).__name__}: {exc}"
            raise


async def execute(
    graph: Any,
    summarize: Callable[[Any], Any],
    history: WorkerHistory,
    run: Run,
) -> Result:
    """Run one op and say how it ended.

    An error raised by the run is a failed result: the client received an error. That says
    nothing about what the run changed before it failed (a lost acknowledgment can follow a
    commit); only the oracle can say. An error after the run completed, while building its
    report, is the harness's own and is raised as HarnessError; so is any failure to write the
    history while the run went on.
    """
    from langgraph.types import Command

    config = {"configurable": {"thread_id": run.thread_id}}
    payload: Any = run.input if run.resume is None else Command(resume=run.resume)
    try:
        with operation(run.op_id):
            output = await graph.ainvoke(payload, config, durability=run.durability)
    except Exception as exc:
        if history.failure is not None:
            raise HarnessError(f"{run.op_id}: {history.failure}") from exc
        kind = type(exc)
        return Result(
            op_id=run.op_id,
            status="fail",
            error=f"{kind.__name__}: {exc}",
            error_type=f"{kind.__module__}.{kind.__qualname__}",
            worker_seq=history.last_seq,
        )
    if history.failure is not None:
        raise HarnessError(f"{run.op_id}: {history.failure}")
    try:
        return Result(
            op_id=run.op_id, status="ok", output=summarize(output), worker_seq=history.last_seq
        )
    except Exception as exc:
        raise HarnessError(
            f"{run.op_id} completed, but its output cannot be reported: {exc}"
        ) from exc


def _workload(name: str) -> tuple[ModuleType, str]:
    try:
        module, builder = WORKLOADS[name]
    except KeyError:
        _fatal(EXIT_STARTUP, f"unknown workload {name!r}; known: {', '.join(sorted(WORKLOADS))}")
    return importlib.import_module(module), builder


def _arm(message: ArmMessage, worker: str) -> Arm:
    try:
        return Arm(
            point=message.point,
            action=message.action,
            worker=worker,
            op_id=message.op_id,
            occurrence=message.occurrence,
            signal=message.signal,
            exit_code=message.exit_code,
        )
    except ValueError as exc:
        raise ProtocolError(f"arm that cannot fire as declared: {exc}") from exc


async def _connect(path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if sys.platform == "win32":
        raise OSError("workers need a POSIX host: Unix sockets and signals")
    return await asyncio.open_unix_connection(str(path), limit=MAX_LINE)


async def serve(config: WorkerConfig) -> int:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from crashcourt.failpoints.saver import wrap

    workload, builder = _workload(config.workload)
    loop = asyncio.get_running_loop()
    try:
        reader, writer = await _connect(config.socket)
    except OSError as exc:
        _fatal(EXIT_STARTUP, f"cannot connect to {config.socket}: {exc}")
    link = Link(writer)
    history = WorkerHistory(config.history, run=config.run, proc=config.name)
    registry = FailpointRegistry(config.name)
    barrier = ControlBarrier(loop, link)

    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        config.dsn,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    try:
        await pool.open(wait=True, timeout=config.connect_timeout)
    except Exception as exc:  # noqa: BLE001 - any failure to connect ends the worker the same way
        _fatal(EXIT_STARTUP, f"cannot open the connection pool: {type(exc).__name__}: {exc}")
    saver = wrap(AsyncPostgresSaver(pool), history=history, registry=registry, barrier=barrier)
    from crashcourt.sink.client import SinkClient
    from crashcourt.workloads.harness import Harness

    build = cast(Callable[[Any, Harness], Any], getattr(workload, builder))
    summarize = cast(Callable[[Any], Any], workload.summarize)
    graph = build(saver, Harness(history, SinkClient(config.sink_url)))

    async def respond(run: Run) -> None:
        try:
            result = await execute(graph, summarize, history, run)
        except HarnessError as exc:
            _fatal(EXIT_HARNESS, str(exc))
        await link.send(result)

    await link.send(Hello(worker=config.name, pid=os.getpid()))
    runs: dict[str, asyncio.Task[None]] = {}
    while True:
        try:
            message = parse_from_orchestrator(await read_line(reader))
            if isinstance(message, ArmMessage):
                registry.arm(_arm(message, config.name))
            elif isinstance(message, Run):
                if message.op_id in runs:
                    raise ProtocolError(f"op {message.op_id} was sent twice")
                runs[message.op_id] = asyncio.create_task(respond(message))
            elif isinstance(message, ReleaseMessage):
                barrier.release(message.hit_seq)
            else:
                break  # shutdown
        except Closed:
            _fatal(EXIT_ORPHANED, "the orchestrator closed the control connection")
        except ProtocolError as exc:
            _fatal(EXIT_PROTOCOL, str(exc))

    if history.failure is not None:  # a background write after the last result
        _fatal(EXIT_HARNESS, history.failure)
    if any(not task.done() for task in runs.values()) or barrier.parked:
        # A run the orchestrator stopped waiting for (it timed out, or is parked for good) is
        # abandoned: it writes nothing more.
        history.close()
        os._exit(EXIT_OK)
    await pool.close(timeout=10)
    history.close()
    writer.close()
    return EXIT_OK


def parse_args(argv: Sequence[str] | None = None) -> WorkerConfig:
    parser = argparse.ArgumentParser(prog="crashcourt.worker")
    parser.add_argument("--name", required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--trial", type=int, required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--sink-url", required=True)
    args = parser.parse_args(argv)
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        _fatal(EXIT_STARTUP, f"{DSN_ENV} is not set")
    return WorkerConfig(
        name=args.name,
        socket=args.socket,
        history=args.history,
        run=RunIdentity(args.batch, args.cell, args.trial, args.seed),
        workload=args.workload,
        dsn=dsn,
        sink_url=args.sink_url,
    )


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv)
    return asyncio.run(serve(config))
