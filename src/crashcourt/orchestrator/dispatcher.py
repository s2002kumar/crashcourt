"""Dispatch and the client view of one trial.

The orchestrator is the client. For each op it sends, it logs `client.invoke` and
`dispatch.send` before the `run` leaves, then exactly one terminal event:

- `client.ok` when the worker's result succeeded, `client.fail` when the result carried an error.
  Both name the worker and the seq of its last event before it sent the result, which the merge
  orders first.
- `client.info` when no result came back: the worker died, its connection was lost, or the run
  passed its timeout. The outcome is unknown, so it is never recorded as `fail`.

`fail` means the client received an error, not that the run changed nothing: a run can commit and
then lose its acknowledgment. What actually happened is the oracle's to say, never the client's.

An op is sent once the op it runs after has its terminal event (or at the start). An op whose
worker is gone by then is never sent and has no client events: nothing was invoked.

Redelivery. A schedule step can hand an op in flight to another worker (`dispatch.redeliver`), as
a job queue does when a consumer dies or stalls. The op keeps its id and its single invoke; from
then on it belongs to the new worker, whose result is its terminal event, and its timeout starts
again. The worker it was taken from may still be running it, held at a barrier: that run is stale.
A result it sends later is noted, never a client event, and the trial does not end while a stale
run that is not held is still going (it gets the op's timeout once it is released).

Everything the engine learns arrives as a note on one queue (a message, a lost connection, a
process exit, a timeout) and is handled one at a time, so the bookkeeping has no races. Barrier
steps fire on hits alone; timeouts only bound how long the client waits and never order anything.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, JsonValue

from crashcourt.history.writer import HistoryWriter
from crashcourt.orchestrator.barriers import BarrierBook, Held, ScheduleError
from crashcourt.spec.events import EventKind
from crashcourt.spec.protocol import Hello, Hit, ReleaseMessage, Result, Run, Shutdown
from crashcourt.spec.scenario import (
    START,
    EndedStep,
    ExitedStep,
    Op,
    Redeliver,
    Redelivery,
    Release,
    Scenario,
)


class StartError(Exception):
    """A worker did not start: no hello, a wrong hello, or it exited first."""


# -- notes: everything the engine reacts to ---------------------------------------------------


@dataclass(frozen=True)
class Received:
    worker: str
    message: Hello | Hit | Result


@dataclass(frozen=True)
class Disconnected:
    """The control connection ended. `partial` is a final line cut off mid-message; `violation`
    says the worker broke the protocol (the connection is then dropped)."""

    worker: str
    partial: bytes = b""
    violation: str | None = None


@dataclass(frozen=True)
class Exited:
    """From waitpid: the worker's status as `subprocess` reports it (negative for a signal)."""

    worker: str
    returncode: int


@dataclass(frozen=True)
class Deadline:
    op_id: str


@dataclass(frozen=True)
class StaleDeadline:
    """A stale run (one whose op was redelivered) was released and has had its time."""

    worker: str
    op_id: str


@dataclass(frozen=True)
class Grace:
    """A worker's connection ended (or it was asked to shut down) and it has had its time."""

    worker: str


Note = Received | Disconnected | Exited | Deadline | StaleDeadline | Grace


class WorkerLink(Protocol):
    """One spawned worker, as the engine drives it. Notes about it go to the engine's queue."""

    name: str
    pid: int

    async def hello(self, timeout: float) -> None:
        """Wait for the worker's hello; StartError if it does not come."""
        ...

    async def send(self, message: BaseModel) -> None:
        """Send one message; ConnectionError if the connection is gone."""
        ...

    async def kill(self, fault_id: str) -> None:
        """SIGKILL as a scheduled fault, recorded as `fault.inject` and `fault.heal`."""
        ...

    def force_kill(self) -> None:
        """SIGKILL a worker that will not exit; harness cleanup, not a fault."""
        ...


def kill_fault_id(step: int) -> str:
    """The fault id a barrier step's kill is recorded under: its index in the schedule."""
    return f"step-{step}"


#: Spawns a worker (logging `worker.start`) whose notes go to the given queue.
Spawn = Callable[[str, asyncio.Queue[Note]], WorkerLink]


#: Linux's numbers for the signals a worker can end by, for hosts whose `signal` lacks them.
LINUX_SIGNALS: dict[int, str] = {
    1: "SIGHUP",
    2: "SIGINT",
    3: "SIGQUIT",
    6: "SIGABRT",
    9: "SIGKILL",
    11: "SIGSEGV",
    15: "SIGTERM",
    19: "SIGSTOP",
}


def describe_status(returncode: int) -> dict[str, JsonValue]:
    """`worker.exit` data for a status: the exit code, or the signal's name."""
    if returncode >= 0:
        return {"returncode": returncode, "exit_code": returncode, "signal": None}
    number = -returncode
    try:
        name = signal.Signals(number).name
    except ValueError:
        name = LINUX_SIGNALS.get(number, f"signal {number}")
    return {"returncode": returncode, "exit_code": None, "signal": name}


def invoke_data(op: Op) -> dict[str, JsonValue]:
    """`client.invoke` data: the worker, and the op's input or the value it resumes with."""
    if op.resume is not None:
        return {"worker": op.worker, "resume": op.resume}
    return {"worker": op.worker, "input": op.input}


def _status_text(returncode: int) -> str:
    status = describe_status(returncode)
    return f"status {returncode}" if status["signal"] is None else str(status["signal"])


# -- state and report -------------------------------------------------------------------------

OpState = Literal["pending", "sent", "done", "skipped"]
Outcome = Literal["ok", "fail", "info"]
_TERMINAL: dict[Outcome, EventKind] = {
    "ok": "client.ok",
    "fail": "client.fail",
    "info": "client.info",
}


@dataclass
class OpRecord:
    op: Op
    thread_id: str
    #: The worker the op belongs to now: the one it was sent to, or the one it was redelivered to.
    worker: str = ""
    #: Workers the op was redelivered away from, in order.
    former: list[str] = field(default_factory=list[str])
    #: What each stale run did: `<worker> answered <status>`, or why it gave no result.
    stale: list[str] = field(default_factory=list[str])
    state: OpState = "pending"
    outcome: Outcome | None = None
    reason: str | None = None
    timed_out: bool = False
    late_result: str | None = None
    timer: asyncio.TimerHandle | None = None


@dataclass
class WorkerRecord:
    link: WorkerLink
    started: bool = False
    connected: bool = False
    exited: bool = False
    returncode: int | None = None
    finalized: bool = False
    shutdown_sent: bool = False
    killed_by_step: str | None = None
    forced: bool = False
    grace: asyncio.TimerHandle | None = None


@dataclass(frozen=True)
class OpReport:
    worker: str
    thread_id: str
    sent: bool
    outcome: Outcome | None
    reason: str | None
    late_result: str | None
    #: The worker the op was redelivered to, if it was.
    redelivered_to: str | None = None
    #: What the run on the worker it was taken from did afterwards.
    stale: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkerReport:
    pid: int
    returncode: int | None
    shutdown_sent: bool
    #: The fault id of the schedule step that killed it, if one did.
    killed_by_step: str | None
    forced: bool


@dataclass(frozen=True)
class EngineReport:
    ops: dict[str, OpReport]
    workers: dict[str, WorkerReport]
    #: Orchestrator steps (every step but a failpoint's), by schedule index.
    fired_steps: list[int]
    unfired_steps: list[int]
    #: The worker broke the protocol or the schedule.
    violations: list[str] = field(default_factory=list[str])
    #: The harness could not run the trial as declared.
    problems: list[str] = field(default_factory=list[str])


# -- the engine -------------------------------------------------------------------------------


class TrialEngine:
    def __init__(
        self,
        scenario: Scenario,
        *,
        history: HistoryWriter,
        thread_prefix: str,
        spawn: Spawn,
    ) -> None:
        self.scenario = scenario
        self.history = history
        self.spawn = spawn
        self.notes: asyncio.Queue[Note] = asyncio.Queue()
        self.book = BarrierBook(scenario)
        self.ops: dict[str, OpRecord] = {
            op.id: OpRecord(op, f"{thread_prefix}/{op.thread}", op.worker) for op in scenario.ops
        }
        self.workers: dict[str, WorkerRecord] = {}
        self.violations: list[str] = []
        self.problems: list[str] = []
        self._shutting_down = False
        steps = scenario.orchestrator_steps()
        self._exit_steps = [(i, s) for i, s in steps if isinstance(s, ExitedStep)]
        self._end_steps = [(i, s) for i, s in steps if isinstance(s, EndedStep)]
        #: Orchestrator steps that fired, in firing order.
        self._fired: list[int] = []
        #: Stale runs not yet answered, by (worker, op): the timer bounding one once released.
        self._stale: dict[tuple[str, str], asyncio.TimerHandle | None] = {}

    async def run(self) -> EngineReport:
        try:
            if await self._start_workers():
                for name in self.scenario.workers:
                    for arm in self.book.arms(name):
                        await self._send(name, arm)
                await self._dispatch_after(START)
                while not self._resolved():
                    await self._handle(await self.notes.get())
        finally:
            await self._shutdown()
        return self._report()

    # -- workers ----------------------------------------------------------------------------

    async def _start_workers(self) -> bool:
        for name in self.scenario.workers:
            link = self.spawn(name, self.notes)
            record = WorkerRecord(link)
            self.workers[name] = record
            try:
                await link.hello(self.scenario.timeouts.hello_s)
            except StartError as exc:
                self.problems.append(f"{name} did not start: {exc}")
                return False
            record.started = record.connected = True
        return True

    def _usable(self, name: str) -> bool:
        record = self.workers.get(name)
        return (
            record is not None
            and record.connected
            and not record.exited
            and not record.shutdown_sent
            and record.killed_by_step is None
        )

    async def _send(self, name: str, message: BaseModel) -> bool:
        record = self.workers[name]
        if not record.connected:
            return False
        try:
            await record.link.send(message)
        except (ConnectionError, OSError):
            return False  # the reader sees the connection end and reports it
        return True

    # -- notes ------------------------------------------------------------------------------

    async def _handle(self, note: Note) -> None:
        if isinstance(note, Received):
            message = note.message
            if isinstance(message, Hit):
                await self._on_hit(note.worker, message)
            elif isinstance(message, Result):
                await self._on_result(note.worker, message)
            else:
                self.violations.append(f"{note.worker} sent hello twice")
        elif isinstance(note, Disconnected):
            await self._on_disconnected(note)
        elif isinstance(note, Exited):
            await self._on_exited(note.worker, note.returncode)
        elif isinstance(note, Deadline):
            await self._on_deadline(note.op_id)
        elif isinstance(note, StaleDeadline):
            self._on_stale_deadline(note)
        else:
            await self._on_grace(note.worker)

    async def _on_hit(self, worker: str, hit: Hit) -> None:
        try:
            self.book.hit(worker, hit)
        except ScheduleError as exc:
            self.violations.append(str(exc))
            return
        if self._shutting_down:
            return  # a run the client gave up on parked again; nothing fires after the trial
        for index, step in self.book.due():
            self._fired.append(index)
            action = step.do
            if isinstance(action, Release):
                await self._release(index, action.release)
            elif isinstance(action, Redeliver):
                waited = [h for h in self.book.held if h.barrier in step.on.all_paused]
                await self._redeliver(index, action.redeliver, waited)
            else:
                await self._kill(action.kill, kill_fault_id(index))

    async def _release(self, index: int, barriers: tuple[str, ...]) -> None:
        held = self.book.release(barriers)
        self._check_late_release(index, held)
        # Every release is logged before any is sent: they go out together.
        for h in held:
            data: dict[str, JsonValue] = {
                "proc": h.worker,
                "hit_seq": h.hit_seq,
                "barrier": h.barrier,
                "step": index,
            }
            self.history.emit("fp.release", data=data)
        for h in held:
            key = (h.worker, str(h.op_id))
            if key in self._stale:
                # A released stale run is waited for, but only for as long as a run is.
                loop = asyncio.get_running_loop()
                self._stale[key] = loop.call_later(
                    self.scenario.timeouts.run_s,
                    self.notes.put_nowait,
                    StaleDeadline(h.worker, str(h.op_id)),
                )
            await self._send(h.worker, ReleaseMessage(hit_seq=h.hit_seq))

    async def _redeliver(
        self, index: int, redelivery: Redelivery, held: Sequence[Held] = ()
    ) -> None:
        """Hand an op in flight to another worker; its run on the old worker, if any, is stale.
        A redelivery that waited for barriers names the hits it waited for, as a release names
        the hit it ends, so the history shows it came after them."""
        record = self.ops[redelivery.op]
        to = redelivery.to
        if record.state != "sent":
            self.problems.append(
                f"step {index}: {record.op.id} was {record.state}, not in flight, when it was to "
                "be redelivered"
            )
            return
        if not self._usable(to):
            self.problems.append(
                f"step {index}: {to} was not running when {record.op.id} was to be redelivered"
            )
            return
        old = record.worker
        data: dict[str, JsonValue] = {"to": to, "from": old, "step": index}
        if held:
            data["held"] = [
                {"proc": h.worker, "hit_seq": h.hit_seq, "barrier": h.barrier} for h in held
            ]
        self.history.emit(
            "dispatch.redeliver", op_id=record.op.id, thread_id=record.thread_id, data=data
        )
        previous = self.workers[old]
        if previous.connected and not previous.exited:
            self._stale[(old, record.op.id)] = None
        record.former.append(old)
        record.worker = to
        self._start_deadline(record)
        await self._send(to, self._run(record))

    def _check_late_release(self, index: int, released: list[Held]) -> None:
        """A run released after its client gave up resumes beside whatever was sent since; the
        timer decided whether it resumed before or after its successors were sent. (Scenarios
        cannot yet make a release wait for a timeout, as a client retry would.)"""
        for held in released:
            record = self.ops.get(held.op_id or "")
            if record is not None and record.timed_out:
                self.problems.append(
                    f"step {index} released {record.op.id} after its client gave up; what "
                    "followed was ordered by a timer"
                )

    async def _kill(self, worker: str, fault_id: str) -> None:
        record = self.workers[worker]
        if record.exited:
            self.problems.append(f"{fault_id}: {worker} had already exited; the kill did nothing")
            return
        record.killed_by_step = fault_id
        try:
            await record.link.kill(fault_id)
        except Exception as exc:  # noqa: BLE001 - a fault that fails makes the trial INVALID
            self.problems.append(f"{fault_id}: killing {worker} failed: {exc}")

    async def _on_result(self, worker: str, result: Result) -> None:
        record = self.ops.get(result.op_id)
        if record is not None and worker in record.former and worker != record.worker:
            # The run the op was taken from: noted, never the client's answer.
            record.stale.append(f"{worker} answered {result.status}")
            handle = self._stale.pop((worker, result.op_id), None)
            if handle is not None:
                handle.cancel()
            return
        if record is None or record.worker != worker or record.state in ("pending", "skipped"):
            self.violations.append(f"{worker} sent a result for {result.op_id}, never sent to it")
            return
        if record.state == "done":
            if record.timed_out and record.late_result is None:
                record.late_result = result.status  # after the client stopped waiting
            else:
                self.violations.append(f"{worker} sent a second result for {result.op_id}")
            return
        # The worker and its last event go together (the merge orders that event first); a
        # worker that wrote nothing before answering is named by the op's dispatch alone.
        data: dict[str, JsonValue] = {}
        if result.worker_seq is not None:
            data.update(worker=worker, worker_seq=result.worker_seq)
        if result.status == "ok":
            data["output"] = result.output
            self._terminal(record, "ok", data=data)
        else:
            data["error_type"] = result.error_type
            self._terminal(record, "fail", data=data, error=result.error)
        await self._ended(record.op.id)

    async def _on_deadline(self, op_id: str) -> None:
        record = self.ops[op_id]
        if record.state != "sent":
            return
        record.timed_out = True
        reason = f"no result within {self.scenario.timeouts.run_s:g} s"
        if not self.book.parked(op_id, record.worker):
            # Parked at a barrier, the run cannot move until a step releases it, so giving up on
            # it orders nothing. Still running, it races whatever is sent next: from here on a
            # timer, not the schedule, decides the order.
            self.problems.append(
                f"{op_id} passed its timeout while running, not parked at a barrier; what "
                "followed was ordered by a timer"
            )
        self._terminal(record, "info", data={"worker": record.worker, "reason": reason})
        await self._ended(op_id)

    def _on_stale_deadline(self, note: StaleDeadline) -> None:
        key = (note.worker, note.op_id)
        if key not in self._stale:
            return
        del self._stale[key]
        self.ops[note.op_id].stale.append(f"{note.worker} gave no result in time")
        self.problems.append(
            f"{note.op_id}'s stale run on {note.worker} gave no result within "
            f"{self.scenario.timeouts.run_s:g} s of its release; what followed was ordered by a "
            "timer"
        )

    async def _on_disconnected(self, note: Disconnected) -> None:
        record = self.workers[note.worker]
        record.connected = False
        if note.violation is not None:
            self.violations.append(f"{note.worker}: {note.violation}")
            if not record.exited and not record.forced:
                record.forced = True
                record.link.force_kill()
        if record.exited:
            await self._finalize(note.worker)
        else:
            self._start_grace(note.worker)

    async def _on_exited(self, worker: str, returncode: int) -> None:
        record = self.workers[worker]
        record.exited = True
        record.returncode = returncode
        data: dict[str, JsonValue] = {"proc": worker, "pid": record.link.pid}
        data.update(describe_status(returncode))
        self.history.emit("worker.exit", data=data)
        for key in [key for key in self._stale if key[0] == worker]:
            handle = self._stale.pop(key)
            if handle is not None:
                handle.cancel()
            self.ops[key[1]].stale.append(f"{worker} exited before answering")
        if not self._shutting_down:
            # The replacement takes the op over before it could end as info for want of a result.
            for index, step in self._exit_steps:
                if step.on.exited == worker and index not in self._fired:
                    self._fired.append(index)
                    await self._redeliver(index, step.do.redeliver)
        if not record.connected:
            await self._finalize(worker)
        else:
            self._start_grace(worker)  # its connection ends with it; this bounds the wait

    async def _on_grace(self, worker: str) -> None:
        record = self.workers[worker]
        record.grace = None
        if record.exited:
            record.connected = False
            await self._finalize(worker)
        elif not record.forced:
            record.forced = True
            self.problems.append(
                f"{worker} did not exit within {self.scenario.timeouts.shutdown_s:g} s "
                + ("of shutdown" if record.shutdown_sent else "of losing its connection")
            )
            record.link.force_kill()
            self._start_grace(worker)
        else:
            # SIGKILL did not end it either: stop waiting, and say so.
            self.problems.append(f"{worker} did not exit even after SIGKILL")
            record.connected = False
            await self._finalize(worker)

    def _start_grace(self, worker: str) -> None:
        record = self.workers[worker]
        if record.grace is None:
            loop = asyncio.get_running_loop()
            record.grace = loop.call_later(
                self.scenario.timeouts.shutdown_s, self.notes.put_nowait, Grace(worker)
            )

    async def _finalize(self, worker: str) -> None:
        """The worker is gone: every op it had not answered ends as info."""
        record = self.workers[worker]
        if record.finalized:
            return
        record.finalized = True
        if record.grace is not None:
            record.grace.cancel()
            record.grace = None
        self.book.drop_worker(worker)
        status = record.returncode
        reason = (
            f"{worker} exited ({_status_text(status)}) before answering"
            if status is not None
            else f"lost the control connection to {worker}"
        )
        unanswered = [r for r in self.ops.values() if r.worker == worker and r.state == "sent"]
        for op in unanswered:
            self._terminal(op, "info", data={"worker": worker, "reason": reason})
        for op in unanswered:
            await self._ended(op.op.id)

    # -- the client view --------------------------------------------------------------------

    def _terminal(
        self,
        record: OpRecord,
        outcome: Outcome,
        *,
        data: dict[str, JsonValue],
        error: str | None = None,
    ) -> None:
        if record.state != "sent":
            raise AssertionError(f"{record.op.id}: a terminal event for an op in {record.state}")
        if record.timer is not None:
            record.timer.cancel()
            record.timer = None
        self.history.emit(
            _TERMINAL[outcome],
            op_id=record.op.id,
            thread_id=record.thread_id,
            status=outcome,
            error=error,
            data=data,
        )
        record.state = "done"
        record.outcome = outcome
        reason = data.get("reason")
        record.reason = reason if isinstance(reason, str) else error

    async def _ended(self, op_id: str) -> None:
        """The op has its terminal event: fire the steps it triggers, then send what follows it."""
        if not self._shutting_down:
            for index, step in self._end_steps:
                if step.on.ended != op_id or index in self._fired:
                    continue
                self._fired.append(index)
                held = {h.barrier for h in self.book.held}
                missing = [name for name in step.do.release if name not in held]
                if missing:
                    self.problems.append(
                        f"step {index}: {', '.join(missing)} not held when {op_id} ended"
                    )
                present = tuple(name for name in step.do.release if name in held)
                if present:
                    await self._release(index, present)
        await self._dispatch_after(op_id)

    async def _dispatch_after(self, op_id: str) -> None:
        for op in self.scenario.ops:
            if op.after == op_id:
                await self._dispatch(self.ops[op.id])

    async def _dispatch(self, record: OpRecord) -> None:
        op = record.op
        if self._shutting_down or not self._usable(op.worker):
            record.state = "skipped"
            record.reason = f"not sent: {op.worker} was no longer running"
            await self._dispatch_after(op.id)
            return
        self.history.emit(
            "client.invoke",
            op_id=op.id,
            thread_id=record.thread_id,
            data=invoke_data(op),
        )
        self.history.emit(
            "dispatch.send", op_id=op.id, thread_id=record.thread_id, data={"to": op.worker}
        )
        record.state = "sent"
        self._start_deadline(record)
        # If the send fails, the worker's death ends the op as info.
        await self._send(op.worker, self._run(record))

    def _run(self, record: OpRecord) -> Run:
        op = record.op
        return Run(
            op_id=op.id,
            thread_id=record.thread_id,
            durability=self.scenario.target.durability,
            input=op.input,
            resume=op.resume,
        )

    def _start_deadline(self, record: OpRecord) -> None:
        if record.timer is not None:
            record.timer.cancel()
        loop = asyncio.get_running_loop()
        record.timer = loop.call_later(
            self.scenario.timeouts.run_s, self.notes.put_nowait, Deadline(record.op.id)
        )

    def _resolved(self) -> bool:
        """Every op has ended or was never sent, and every stale run still going has answered.
        A stale run held at a barrier is not waited for: nothing would release it."""
        if not all(r.state in ("done", "skipped") for r in self.ops.values()):
            return False
        return all(self.book.parked(op_id, worker) for worker, op_id in self._stale)

    # -- the end of the trial ---------------------------------------------------------------

    async def _shutdown(self) -> None:
        self._shutting_down = True
        for record in self.ops.values():
            if record.timer is not None:
                record.timer.cancel()
                record.timer = None
        for (worker, op_id), handle in self._stale.items():
            if handle is not None:
                handle.cancel()
            self.ops[op_id].stale.append(f"{worker} gave no result before the trial ended")
        self._stale.clear()
        for name, record in self.workers.items():
            if record.connected and not record.exited:
                record.shutdown_sent = await self._send(name, Shutdown())
            if not record.exited:
                self._start_grace(name)
        while not all(r.finalized for r in self.workers.values()):
            await self._handle(await self.notes.get())

    def _report(self) -> EngineReport:
        ops = {
            op_id: OpReport(
                worker=r.op.worker,
                thread_id=r.thread_id,
                sent=r.state == "done",
                outcome=r.outcome,
                reason=r.reason,
                late_result=r.late_result,
                redelivered_to=r.worker if r.former else None,
                stale=tuple(r.stale),
            )
            for op_id, r in self.ops.items()
        }
        workers = {
            name: WorkerReport(
                pid=r.link.pid,
                returncode=r.returncode,
                shutdown_sent=r.shutdown_sent,
                killed_by_step=r.killed_by_step,
                forced=r.forced,
            )
            for name, r in self.workers.items()
        }
        return EngineReport(
            ops=ops,
            workers=workers,
            fired_steps=list(self._fired),
            unfired_steps=[
                i for i, _ in self.scenario.orchestrator_steps() if i not in self._fired
            ],
            violations=list(self.violations),
            problems=list(self.problems),
        )
