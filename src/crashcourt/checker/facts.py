"""Shared definitions over one trial's trusted records (red-team specification 2.0).

Every definition here reads the per-process histories through the trusted order, the ledger, and
the snapshot's raw rows. None reads a clock. Where an event's `data` layout is not fixed yet
(T-07 writes the orchestrator's fault, exit, and dispatch events), the readers below accept the
layouts T-04's sample and T-05's fault primitives use, and nothing else.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import JsonValue

from crashcourt.checker.artifacts import LedgerRow, OpSpec, Snapshot, TrialScenario
from crashcourt.checker.order import Order, ref
from crashcourt.history.merge import Ref
from crashcourt.spec.events import Event

#: LangGraph's null task: writes applied before any task (a Command's resume value, for one).
NULL_TASK_ID = "00000000-0000-0000-0000-000000000000"
INTERRUPT = "__interrupt__"
RESUME = "__resume__"
ERROR = "__error__"
#: Channels LangGraph stores with a negative idx (WRITES_IDX_MAP); every other write is regular.
SPECIAL_CHANNELS = frozenset({ERROR, "__scheduled__", INTERRUPT, RESUME})

#: Linux signal numbers, for exit statuses reported as a negative return code.
_SIGNALS = {
    1: "SIGHUP",
    2: "SIGINT",
    3: "SIGQUIT",
    6: "SIGABRT",
    9: "SIGKILL",
    11: "SIGSEGV",
    15: "SIGTERM",
    19: "SIGSTOP",
}
_STOP_FAULTS = {"kill": "SIGKILL", "stop": "SIGSTOP", "terminate": "SIGTERM"}
#: T-05's connection faults that are stops (2.0: reset, terminate, restart).
_CONNECTION_FAULTS = frozenset({"reset_connections", "terminate_backends", "restart_database"})


def pointer(event: Event | Ref) -> str:
    """An evidence pointer to one history event."""
    return f"history/{event.proc}.jsonl#seq={event.seq}"


def ledger_pointer(row: LedgerRow) -> str:
    return f"ledger#sink_seq={row.sink_seq}"


def checkpoint_pointer(thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
    return f"snapshot#checkpoint={thread_id}|{checkpoint_ns}|{checkpoint_id}"


def data_text(event: Event, key: str) -> str | None:
    value = event.data.get(key)
    return value if isinstance(value, str) and value else None


def data_int(event: Event, key: str) -> int | None:
    value = event.data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def data_list(event: Event, key: str) -> tuple[JsonValue, ...]:
    value = event.data.get(key)
    return tuple(value) if isinstance(value, list) else ()


def node_name(event: Event) -> str | None:
    """The node a node.start or node.end names (data.node, recorded by the workload)."""
    return data_text(event, "node")


def regular_channels(event: Event) -> tuple[str, ...]:
    return tuple(
        c for c in data_list(event, "channels") if isinstance(c, str) and c not in SPECIAL_CHANNELS
    )


@dataclass(frozen=True)
class Exit:
    worker: str
    event: Event
    signal: str | None
    returncode: int | None

    @property
    def abnormal(self) -> bool:
        return self.signal is not None or (self.returncode is not None and self.returncode != 0)


@dataclass(frozen=True)
class Stop:
    """A worker stopped: killed, stopped, exited, or cut off from the database.

    `position` is the seq of the worker's last event before the stop, when the records fix it.
    """

    worker: str
    kind: str
    source: Event
    position: int | None


@dataclass(frozen=True)
class NodeRun:
    node: str | None
    task_id: str | None
    start: Event


@dataclass(frozen=True)
class Ack:
    """An acknowledged checkpoint: a saver.put.return with status ok."""

    worker: str
    event: Event
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str


@dataclass(frozen=True)
class OpRecord:
    spec: OpSpec
    invokes: tuple[Event, ...]
    terminals: tuple[Event, ...]
    dispatches: tuple[Event, ...]


@dataclass(frozen=True)
class Commit:
    """Evidence that a task's regular writes committed, and the worker event that shows it."""

    task_id: str
    worker: str
    witness: Event
    evidence: tuple[str, ...]


class TrialFacts:
    def __init__(
        self,
        scenario: TrialScenario,
        order: Order,
        *,
        orchestrator: str,
        snapshot: Snapshot,
        ledger: Sequence[LedgerRow],
        committed_calls: Iterable[Ref] = (),
    ) -> None:
        self.scenario = scenario
        self.order = order
        self.orchestrator = orchestrator
        self.snapshot = snapshot
        self.ledger = tuple(sorted(ledger, key=lambda r: r.sink_seq))
        self.committed_calls = frozenset(committed_calls)
        by_proc = order.by_proc
        self.orch_events: tuple[Event, ...] = by_proc.get(orchestrator, ())
        self.workers: tuple[str, ...] = tuple(
            sorted(p for p, events in by_proc.items() if p != orchestrator and _is_worker(events))
        )
        self.pid_to_proc: dict[int, str] = {
            by_proc[w][0].pid: w for w in self.workers if by_proc[w]
        }
        self.exits: dict[str, Exit] = {}
        for event in self.orch_events:
            if event.kind == "worker.exit" and (proc := data_text(event, "proc")) is not None:
                self.exits.setdefault(proc, _exit(proc, event))
        self.ops: dict[str, OpRecord] = {}
        for op in scenario.ops:
            self.ops[op.op_id] = OpRecord(
                spec=op,
                invokes=self._orch("client.invoke", op.op_id),
                terminals=tuple(
                    e
                    for e in self.orch_events
                    if e.op_id == op.op_id and e.kind in {"client.ok", "client.fail", "client.info"}
                ),
                dispatches=tuple(
                    e
                    for e in self.orch_events
                    if e.op_id == op.op_id and e.kind in {"dispatch.send", "dispatch.redeliver"}
                ),
            )
        self.stops: tuple[Stop, ...] = tuple(self._stops())
        self.acks: tuple[Ack, ...] = tuple(
            Ack(e.proc, e, e.thread_id, e.checkpoint_ns or "", e.checkpoint_id)
            for w in self.workers
            for e in by_proc[w]
            if e.kind == "saver.put.return"
            and e.status == "ok"
            and e.thread_id is not None
            and e.checkpoint_id is not None
        )
        links: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        for w in self.workers:
            for e in by_proc[w]:
                if (
                    e.kind == "saver.put_writes.call"
                    and e.task_id not in (None, NULL_TASK_ID)
                    and e.thread_id is not None
                    and e.checkpoint_id is not None
                ):
                    links[str(e.task_id)].add((e.thread_id, e.checkpoint_ns or "", e.checkpoint_id))
        for row in snapshot.writes:
            if row.task_id != NULL_TASK_ID:
                links[row.task_id].add((row.thread_id, row.checkpoint_ns, row.checkpoint_id))
        #: task_id -> the checkpoint(s) its writes are linked to (the checkpoint whose next step
        #: the task belongs to).
        self.task_checkpoints: dict[str, tuple[tuple[str, str, str], ...]] = {
            t: tuple(sorted(c)) for t, c in links.items()
        }

    # -- operations and threads ---------------------------------------------------------------

    def _orch(self, kind: str, op_id: str) -> tuple[Event, ...]:
        return tuple(e for e in self.orch_events if e.kind == kind and e.op_id == op_id)

    def thread_of(self, event: Event) -> str | None:
        if event.thread_id is not None:
            return event.thread_id
        op = self.scenario.op(event.op_id)
        return op.thread_id if op is not None else None

    def thread_ops(self, thread_id: str) -> tuple[OpRecord, ...]:
        return tuple(r for r in self.ops.values() if r.spec.thread_id == thread_id)

    def dispatches_on(self, thread_id: str) -> tuple[Event, ...]:
        return tuple(
            sorted((d for r in self.thread_ops(thread_id) for d in r.dispatches), key=_seq)
        )

    def writers(self, thread_id: str) -> tuple[str, ...]:
        return tuple(sorted({data_text(d, "to") or "" for d in self.dispatches_on(thread_id)}))

    def resume_invokes(self, thread_id: str) -> tuple[Event, ...]:
        return tuple(
            i for r in self.thread_ops(thread_id) if r.spec.kind == "resume" for i in r.invokes
        )

    def concurrency(self, thread_id: str) -> tuple[str, ...]:
        """Evidence of concurrent writers on the thread; empty for a single-writer thread.

        Two workers overlap when one is dispatched before the other finished its op: before the
        op's terminal client event and before the worker exited. A killed worker followed by a
        replacement is sequential. A worker stopped (SIGSTOP injected, no SIGCONT yet) while
        another worker of the thread is dispatched is concurrent.
        """
        evidence: list[str] = []
        dispatches = self.dispatches_on(thread_id)
        for i, first in enumerate(dispatches):
            first_worker = data_text(first, "to")
            busy_until = self._busy_until(first_worker, str(first.op_id), first.seq)
            for second in dispatches[i + 1 :]:
                if data_text(second, "to") != first_worker and second.seq < busy_until:
                    evidence.extend((pointer(first), pointer(second)))
        writers = set(self.writers(thread_id))
        for inject in self.orch_events:
            if inject.kind != "fault.inject" or _stop_kind(inject) != "SIGSTOP":
                continue
            stopped = self.fault_worker(inject)
            if stopped not in writers:
                continue
            heal = self.heal_of(inject)
            until = heal.seq if heal is not None else math.inf
            for d in dispatches:
                if inject.seq < d.seq < until and data_text(d, "to") != stopped:
                    evidence.extend((pointer(inject), pointer(d)))
        return tuple(dict.fromkeys(evidence))

    def _busy_until(self, worker: str | None, op_id: str, after: int) -> float:
        ends = [t.seq for t in self.ops[op_id].terminals] if op_id in self.ops else []
        if worker is not None and worker in self.exits:
            ends.append(self.exits[worker].event.seq)
        later = [s for s in ends if s > after]
        return min(later) if later else math.inf

    def disclaimed(self, thread_id: str) -> tuple[str, ...]:
        """Evidence that LGP-DT disclaims the thread: concurrent writers or more than one resume."""
        evidence = list(self.concurrency(thread_id))
        resumes = self.resume_invokes(thread_id)
        if len(resumes) > 1:
            evidence.extend(pointer(i) for i in resumes)
        return tuple(dict.fromkeys(evidence))

    def harness_events(self, thread_id: str) -> tuple[str, ...]:
        """Harness-originated events on the thread: redeliveries, and client.invoke events that
        repeat an earlier invoke's logical op."""
        evidence: list[str] = []
        seen: set[str] = set()
        for event in self.orch_events:
            if self.thread_of(event) != thread_id:
                continue
            if event.kind == "dispatch.redeliver":
                evidence.append(pointer(event))
            elif event.kind == "client.invoke":
                logical = event.logical_op or self._logical_op(event.op_id)
                if logical in seen:
                    evidence.append(pointer(event))
                if logical is not None:
                    seen.add(logical)
        return tuple(evidence)

    def _logical_op(self, op_id: str | None) -> str | None:
        op = self.scenario.op(op_id)
        return op.logical_op if op is not None else None

    # -- faults, exits, and stops -------------------------------------------------------------

    def fault_worker(self, event: Event) -> str | None:
        """The worker a fault.inject targets: data.proc, data.worker, the worker behind data.proxy
        (the scenario's proxy map), or data.pid."""
        name = data_text(event, "proc") or data_text(event, "worker")
        if name is not None:
            return name
        proxy = data_text(event, "proxy")
        if proxy is not None:
            return self.scenario.proxies.get(proxy)
        pid = data_int(event, "pid")
        return self.pid_to_proc.get(pid) if pid is not None else None

    def heal_of(self, inject: Event) -> Event | None:
        return next(
            (
                e
                for e in self.orch_events
                if e.kind == "fault.heal" and e.fault_id == inject.fault_id and e.seq > inject.seq
            ),
            None,
        )

    def _stops(self) -> list[Stop]:
        stops: list[Stop] = []
        for worker in self.workers:
            for event in self.order.by_proc[worker]:
                action = data_text(event, "action")
                if event.kind == "fp.action" and action in {"exit", "signal"}:
                    kind = data_text(event, "signal") if action == "signal" else "exit"
                    stops.append(Stop(worker, kind or "signal", event, event.seq))
        # 2.0: a worker.exit with a signal is a stop. A non-zero status without a signal is not
        # (an exit at a failpoint is already the fp.action above).
        for worker, exit_ in sorted(self.exits.items()):
            if exit_.signal is not None and worker in self.order.by_proc:
                last = len(self.order.by_proc[worker]) - 1
                stops.append(Stop(worker, exit_.signal or "exit", exit_.event, last))
        for event in self.orch_events:
            if event.kind != "fault.inject":
                continue
            worker = self.fault_worker(event)
            kind = _stop_kind(event)
            if kind is not None and worker in self.order.by_proc:
                before = [
                    e.seq
                    for e in self.order.by_proc[worker]
                    if self.order.before(ref(e), ref(event))
                ]
                if before:
                    position: int | None = max(before)
                elif kind in {"SIGKILL", "SIGTERM"}:
                    position = len(self.order.by_proc[worker]) - 1
                else:
                    position = None
                stops.append(Stop(worker, kind, event, position))
            elif (data_text(event, "fault") or "") in _CONNECTION_FAULTS:
                targets = [worker] if worker is not None else list(self.workers)
                stops.extend(Stop(w, str(data_text(event, "fault")), event, None) for w in targets)
        return stops

    def stops_of(self, worker: str) -> tuple[Stop, ...]:
        return tuple(s for s in self.stops if s.worker == worker)

    def running_at(self, stop: Stop) -> tuple[NodeRun, ...]:
        """Nodes with a node.start and no node.end in the worker at or before the stop."""
        if stop.position is None:
            return ()
        open_runs: dict[tuple[str | None, str | None], NodeRun] = {}
        for event in self.order.by_proc[stop.worker][: stop.position + 1]:
            key = (event.task_id, None if event.task_id else node_name(event))
            if event.kind == "node.start":
                open_runs[key] = NodeRun(node_name(event), event.task_id, event)
            elif event.kind == "node.end":
                open_runs.pop(key, None)
        return tuple(open_runs.values())

    def after(self, earlier: Event, later: Event) -> bool:
        """Whether `earlier` happens before `later` in the trusted order."""
        return self.order.before(ref(earlier), ref(later))

    # -- effects -------------------------------------------------------------------------------

    def request_for(self, row: LedgerRow) -> Event | None:
        """The worker's effect.request for a ledger row."""
        return next(
            (
                e
                for e in self.order.by_proc.get(row.worker, ())
                if e.kind == "effect.request"
                and e.op_id == row.op_id
                and e.effect_key == row.effect_key
                and e.attempt == row.attempt
            ),
            None,
        )

    def enclosing_node(self, event: Event) -> NodeRun | None:
        """The node run in progress in the event's process when the event was written."""
        open_runs: dict[tuple[str | None, str | None], NodeRun] = {}
        for e in self.order.by_proc[event.proc][: event.seq]:
            key = (e.task_id, None if e.task_id else node_name(e))
            if e.kind == "node.start":
                open_runs[key] = NodeRun(node_name(e), e.task_id, e)
            elif e.kind == "node.end":
                open_runs.pop(key, None)
        runs = sorted(open_runs.values(), key=lambda r: r.start.seq)
        return runs[-1] if runs else None

    # -- task writes ---------------------------------------------------------------------------

    def interrupt_evidence(self, task_id: str) -> tuple[str, ...]:
        """An INTERRUPT write for the task: a snapshot row, or a put_writes call carrying it."""
        evidence = [
            f"snapshot#write={w.thread_id}|{w.checkpoint_ns}|{w.checkpoint_id}|{w.task_id}|{w.idx}"
            for w in self.snapshot.writes
            if w.task_id == task_id and w.channel == INTERRUPT
        ]
        evidence.extend(
            pointer(e)
            for w in self.workers
            for e in self.order.by_proc[w]
            if e.kind == "saver.put_writes.call"
            and e.task_id == task_id
            and INTERRUPT in data_list(e, "channels")
        )
        return tuple(evidence)

    def commits(self, task_id: str) -> tuple[Commit, ...]:
        """Worker evidence that the task's regular writes committed, each backed by snapshot rows.

        Routes: the put_writes call returned ok; the put_writes.after failpoint was hit (the inner
        call had returned); or a lost-ack fault on the call verified the commit (3.6).
        """
        rows = [
            w
            for w in self.snapshot.writes
            if w.task_id == task_id and w.idx >= 0 and w.channel not in SPECIAL_CHANNELS
        ]
        if not rows:
            return ()
        row_evidence = tuple(
            f"snapshot#write={w.thread_id}|{w.checkpoint_ns}|{w.checkpoint_id}|{w.task_id}|{w.idx}"
            for w in rows
        )
        found: list[Commit] = []
        for worker in self.workers:
            events = self.order.by_proc[worker]
            for call in events:
                if (
                    call.kind != "saver.put_writes.call"
                    or call.task_id != task_id
                    or not regular_channels(call)
                ):
                    continue
                witness = self._commit_witness(events, call)
                if witness is not None:
                    found.append(
                        Commit(task_id, worker, witness, (pointer(witness), *row_evidence))
                    )
        return tuple(found)

    def _commit_witness(self, events: Sequence[Event], call: Event) -> Event | None:
        if ref(call) in self.committed_calls:
            return call
        for later in events[call.seq + 1 :]:
            if later.task_id != call.task_id:
                continue
            if later.kind == "saver.put_writes.return" and later.status == "ok":
                return later
            if later.kind == "fp.hit" and later.fp == "put_writes.after" and later.status != "fail":
                return later
            if later.kind in {"saver.put_writes.raise", "saver.put_writes.call"}:
                return None
        return None

    def interruption(self, commit: Commit) -> Event | None:
        """What ended the committed attempt: a stop of the worker after the commit witness, or the
        op's terminal client event being a failure or an unknown outcome."""
        for stop in self.stops_of(commit.worker):
            if stop.position is not None and stop.position >= commit.witness.seq:
                return stop.source
        record = self.ops.get(str(commit.witness.op_id))
        if record is not None:
            for terminal in record.terminals:
                if terminal.kind in {"client.fail", "client.info"}:
                    return terminal
        return None

    def error_write_beside(self, task_id: str) -> tuple[str, ...]:
        """An ERROR write for another task on a checkpoint the task's writes are linked to: a node
        failed in the same super-step (LangGraph saves it before raising)."""
        linked = set(self.task_checkpoints.get(task_id, ()))
        return tuple(
            f"snapshot#write={w.thread_id}|{w.checkpoint_ns}|{w.checkpoint_id}|{w.task_id}|{w.idx}"
            for w in self.snapshot.writes
            if w.channel == ERROR
            and w.task_id != task_id
            and (w.thread_id, w.checkpoint_ns, w.checkpoint_id) in linked
        )


def _seq(event: Event) -> int:
    return event.seq


def _is_worker(events: Sequence[Event]) -> bool:
    kinds = {e.kind for e in events}
    return not any(k.startswith(("client.", "dispatch.")) or k == "sink.append" for k in kinds)


def _exit(worker: str, event: Event) -> Exit:
    signal = data_text(event, "signal")
    returncode = data_int(event, "returncode")
    if returncode is None:
        returncode = data_int(event, "exit_code")
    if signal is None and returncode is not None and returncode < 0:
        signal = _SIGNALS.get(-returncode, f"signal {-returncode}")
    return Exit(worker, event, signal, returncode)


def _stop_kind(event: Event) -> str | None:
    signal = data_text(event, "signal")
    if signal in {"SIGKILL", "SIGSTOP", "SIGTERM"}:
        return signal
    return _STOP_FAULTS.get(data_text(event, "fault") or "")
