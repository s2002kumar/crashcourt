"""Demo artifacts for the evidence explorer, written by `crashcourt fixtures --demo`.

Nothing here is a result. The histories are scripted: no process ran, no fault was injected, no
database was read, and the outcomes are typed in so each page of the explorer has something to
render. They exist so the site can be built before any batch has run, and they are labeled so
they can never be mistaken for one:

- every identifier starts with DEMO- (batch, cell, op, logical op, thread, checkpoint, task,
  effect key, fault, invariant, clause, repro, and the manifest's commit, hashes, and images);
- every batch and result record carries provenance "fixture";
- every outcome's note says it was written by this generator, not measured;
- the runtimes are demo runtimes (DEMO-runtime-a, DEMO-runtime-b), so no fixture describes a
  real system's behavior, and no fixture carries a VIOLATION, which needs a human review.

Every file is one of the four v1 artifacts (manifest.json, batch.json, result.json, and .jsonl
histories), so the site validates fixtures exactly as it validates a harness batch, and the
histories are merged by the harness's own merger. The output depends only on the seed: clocks,
pids, and trial seeds come from sha256, never from the machine.

Layout: `<batch>/manifest.json`, `<batch>/batch.json`, and per trial `<batch>/<cell>/<trial>/`
holding `result.json`, one history per process, and `merged.jsonl`. Evidence in a result points
into the merged history as `<cell>/<trial>/merged.jsonl#/<index>`, a JSON pointer into the
history read as an array of events.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from crashcourt.fixtures.scenario import COMPARE, DEMO, MATRIX, DemoCell, DemoScenario
from crashcourt.history.merge import merge
from crashcourt.manifest import Manifest
from crashcourt.spec.content import content_sha256
from crashcourt.spec.events import Event
from crashcourt.spec.records import BatchRecord, InvariantResult, ResultRecord

DEFAULT_SEED = 1

#: The note on every outcome, so a rendered outcome says where it came from.
NOTE = "Demo data, not a result: written by crashcourt fixtures --demo, not measured."

#: 2027-01-15T08:00:00Z. Clocks have real magnitudes so the site's exact-integer path is used:
#: wall_ns is about 1.8e18, and mono_ns (about 107 days of uptime) is past 2**53 too.
WALL_BASE = 1_800_000_000_000_000_000
MONO_BASE = 9_216_000_000_000_000
SECOND = 1_000_000_000
MS = 1_000_000

ORCH, WORKER_A, WORKER_B, SINK = "orchestrator", "worker-a", "worker-b", "sink"


@dataclass(frozen=True)
class DemoBatch:
    """One fixture batch: a demonstration scenario run against a demo runtime."""

    batch: str
    #: The target segment of each cell id.
    target: str
    #: The manifest's pinned packages: a demo runtime, never a real one.
    packages: Mapping[str, str]
    scenario: DemoScenario
    #: When the batch started, relative to WALL_BASE.
    start_ns: int


BATCHES: tuple[DemoBatch, ...] = (
    DemoBatch(
        batch="DEMO-batch-matrix",
        target="pg-async",
        packages={"DEMO-runtime-a": "1.0.0", "DEMO-runtime-a-saver-postgres": "1.0.0"},
        scenario=MATRIX,
        start_ns=0,
    ),
    DemoBatch(
        batch="DEMO-batch-compare-a",
        target="rt-a",
        packages={"DEMO-runtime-a": "1.0.0", "DEMO-runtime-a-saver-postgres": "1.0.0"},
        scenario=COMPARE,
        start_ns=-2 * 3600 * SECOND,
    ),
    DemoBatch(
        batch="DEMO-batch-compare-b",
        target="rt-b",
        packages={"DEMO-runtime-b": "1.0.0"},
        scenario=COMPARE,
        start_ns=-3 * 3600 * SECOND,
    ),
)


def cell_id(batch: DemoBatch, cell: DemoCell) -> str:
    """`DEMO-[control-]<target>-<durability>-<workload>-<fault>[-rand]`."""
    parts = [cell.control, batch.target, cell.durability, cell.workload, cell.fault or "none"]
    name = "-".join(p for p in parts if p)
    return f"{DEMO}{name}{'-rand' if cell.kind == 'randomized' else ''}"


def scenario_sha256(scenario: DemoScenario) -> str:
    """The batch record's scenario hash: the content hash of the demonstration scenario."""
    return content_sha256(scenario)


def trial_seed(master: int, cell: str, trial: int) -> str:
    """A trial's seed from the batch's master seed, the way every trial records one."""
    return hashlib.sha256(f"{master}\x00{cell}\x00{trial}".encode()).hexdigest()


class _Rng:
    """Deterministic integers from sha256: the same on every platform and Python version."""

    def __init__(self, *key: object) -> None:
        self._key = "\x00".join(str(k) for k in key)
        self._n = 0

    def between(self, lo: int, hi: int) -> int:
        digest = hashlib.sha256(f"{self._key}\x00{self._n}".encode()).digest()
        self._n += 1
        return lo + int.from_bytes(digest[:8], "big") % (hi - lo + 1)


@dataclass(frozen=True)
class _Outcome:
    """An outcome as a story states it; evidence names events, resolved to pointers later."""

    invariant: str
    outcome: str
    clause: str | None = None
    evidence: tuple[Event, ...] = ()
    repro: str | None = None
    reason: str | None = None


@dataclass
class _Trial:
    """One scripted trial: per-process histories on one host clock."""

    batch: str
    cell: str
    trial: int
    seed: str
    rng: _Rng
    mono: int
    pids: Mapping[str, int]
    thread: str
    histories: dict[str, list[Event]] = field(default_factory=dict[str, list[Event]], init=False)
    counters: dict[str, int] = field(default_factory=dict[str, int], init=False)

    def next(self, name: str) -> int:
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]

    def wait(self, lo_ns: int, hi_ns: int) -> None:
        self.mono += self.rng.between(lo_ns, hi_ns)

    def emit(self, proc: str, kind: str, **fields: Any) -> Event:
        self.wait(40_000, 1_400_000)
        history = self.histories.setdefault(proc, [])
        event = Event.model_validate(
            {
                "v": 1,
                "batch": self.batch,
                "cell": self.cell,
                "trial": self.trial,
                "seed": self.seed,
                "proc": proc,
                "pid": self.pids[proc],
                "seq": len(history),
                "mono_ns": self.mono,
                "wall_ns": self.mono - MONO_BASE + WALL_BASE,
                "kind": kind,
                **fields,
            }
        )
        history.append(event)
        return event

    @property
    def where(self) -> dict[str, Any]:
        return {"thread_id": self.thread, "checkpoint_ns": ""}


# -- steps every story is built from ---------------------------------------------------------


def _start_workers(t: _Trial, *workers: str) -> None:
    for worker in workers:
        t.emit(ORCH, "worker.start", data={"proc": worker, "pid": t.pids[worker]})


def _exit_workers(t: _Trial, *workers: str) -> None:
    for worker in workers:
        t.emit(ORCH, "worker.exit", data={"proc": worker, "returncode": 0})


def _invoke(t: _Trial, logical: str, worker: str, resume: str | None = None) -> str:
    op = f"{DEMO}op-{t.next('op')}"
    data: dict[str, Any] = {} if resume is None else {"resume": resume}
    t.emit(ORCH, "client.invoke", op_id=op, logical_op=logical, data=data)
    t.emit(ORCH, "dispatch.send", op_id=op, logical_op=logical, data={"to": worker})
    return op


def _answer(t: _Trial, op: str, last: Event, status: str = "ok", error: str | None = None) -> None:
    """The client's terminal event for a result the worker sent right after `last`."""
    t.emit(
        ORCH,
        "client.ok" if status == "ok" else "client.fail",
        op_id=op,
        status=status,
        error=error,
        data={"worker": last.proc, "worker_seq": last.seq},
    )


def _read(t: _Trial, worker: str, op: str, head: str | None) -> None:
    t.emit(worker, "saver.get_tuple.call", op_id=op, **t.where)
    t.emit(worker, "saver.get_tuple.return", op_id=op, checkpoint_id=head, status="ok", **t.where)


def _hit(t: _Trial, worker: str, op: str, fp: str) -> Event:
    return t.emit(worker, "fp.hit", op_id=op, fp=fp, **t.where)


def _release(t: _Trial, *hits: Event, trigger: dict[str, Any] | None = None) -> None:
    """Release paused workers together; then each logs its failpoint's action."""
    why = trigger or {"all_paused": [h.proc for h in hits]}
    for hit in hits:
        t.emit(ORCH, "fp.release", data={"proc": hit.proc, "hit_seq": hit.seq, "trigger": why})
    for hit in hits:
        t.emit(hit.proc, "fp.action", op_id=hit.op_id, data={"hit_seq": hit.seq, "action": "pause"})


def _put_fields(t: _Trial, op: str, child: str, parent: str | None, step: int) -> dict[str, Any]:
    return {
        "op_id": op,
        "checkpoint_id": child,
        "parent_checkpoint_id": parent,
        **t.where,
        "data": {"step": step, "source": "loop", "channels": ["messages", "state"]},
    }


def _put(t: _Trial, worker: str, op: str, child: str, parent: str | None, step: int) -> Event:
    fields = _put_fields(t, op, child, parent, step)
    t.emit(worker, "saver.put.call", **fields)
    return t.emit(worker, "saver.put.return", status="ok", **fields)


def _start(t: _Trial, worker: str, op: str, node: str, attempt: int = 1) -> str:
    task = f"{DEMO}task-{t.next('task')}"
    t.emit(worker, "node.start", op_id=op, attempt=attempt, task_id=task, data={"node": node})
    return task


def _writes(
    t: _Trial, worker: str, op: str, head: str | None, task: str, channel: str, path: str
) -> Event:
    fields: dict[str, Any] = {"op_id": op, "checkpoint_id": head, "task_id": task, **t.where}
    data: dict[str, Any] = {"channels": [channel], "task_path": path}
    t.emit(worker, "saver.put_writes.call", **fields, data=data)
    return t.emit(worker, "saver.put_writes.return", status="ok", **fields, data=data)


def _node(
    t: _Trial,
    worker: str,
    op: str,
    node: str,
    head: str | None,
    child: str,
    step: int,
    *,
    attempt: int = 1,
    effect: Callable[[], object] | None = None,
) -> Event:
    """Run one node: start, its effect if any, its writes, end, and the checkpoint after it."""
    task = _start(t, worker, op, node, attempt)
    if effect is not None:
        effect()
    _writes(t, worker, op, head, task, "state", f"~__pregel_pull, {node}")
    t.emit(worker, "node.end", op_id=op, attempt=attempt, task_id=task, data={"node": node})
    return _put(t, worker, op, child, head, step)


@dataclass(frozen=True)
class _Effect:
    request: Event
    append: Event
    response: Event


def _effect(
    t: _Trial,
    worker: str,
    op: str,
    logical: str,
    key: str,
    attempt: int,
    seen: str,
    node: str,
    decision: str | None = None,
) -> _Effect:
    """An effect the sink records: the request, the ledger row, and the acknowledgment."""
    row = t.next("sink_seq")
    payload = hashlib.sha256(f"{key}\x00{attempt}".encode()).hexdigest()
    fields: dict[str, Any] = {
        "op_id": op,
        "logical_op": logical,
        "effect_key": key,
        "attempt": attempt,
        "thread_id": t.thread,
        "checkpoint_id": seen,
    }
    data: dict[str, Any] = {"node": node, "payload_hash": payload}
    if decision is not None:
        data["decision"] = decision
    request = t.emit(worker, "effect.request", **fields, data=data)
    row_hash = hashlib.sha256(f"{t.seed}\x00{row}".encode()).hexdigest()
    append = t.emit(
        SINK,
        "sink.append",
        **fields,
        status="ok",
        data={"worker": worker, "sink_seq": row, "row_hash": row_hash},
    )
    response = t.emit(worker, "effect.response", **fields, status="ok", data={"sink_seq": row})
    return _Effect(request, append, response)


def _fault(t: _Trial, action: dict[str, Any], trigger: dict[str, Any]) -> str:
    fault = f"{DEMO}fault-{t.next('fault')}"
    t.emit(ORCH, "fault.inject", fault_id=fault, data={**action, "trigger": trigger})
    return fault


def _heal(t: _Trial, fault: str, data: dict[str, Any]) -> None:
    t.emit(ORCH, "fault.heal", fault_id=fault, status="ok", data=data)


def _cp(n: int | str) -> str:
    return f"{DEMO}cp-{n}"


def _to_interrupt(t: _Trial, worker: str) -> str:
    """W1 up to its approval interrupt: plan, then approval saves it. Returns the head."""
    op = _invoke(t, f"{DEMO}start-{t.trial}", worker)
    _read(t, worker, op, None)
    _node(t, worker, op, "plan", None, _cp(1), 1)
    task = _start(t, worker, op, "approval")
    last = _writes(t, worker, op, _cp(1), task, "__interrupt__", "~__pregel_pull, approval")
    _answer(t, op, last)
    return _cp(1)


@dataclass(frozen=True)
class _Run:
    approved: Event
    effect: _Effect
    last: Event


def _approve_run(
    t: _Trial, worker: str, op: str, logical: str, head: str, attempt: int, label: str = ""
) -> _Run:
    """W1 from the resumed approval to the end: approval, execute with its charge, record."""
    approved = _node(t, worker, op, "approval", head, _cp(f"2{label}"), 2)
    key = f"{DEMO}charge-order-{t.trial}"
    seen = _cp(f"2{label}")
    effects: list[_Effect] = []

    def charge() -> None:
        effects.append(_effect(t, worker, op, logical, key, attempt, seen, "execute", "approve"))

    _node(t, worker, op, "execute", seen, _cp(f"3{label}"), 3, attempt=attempt, effect=charge)
    last = _node(t, worker, op, "record", _cp(f"3{label}"), _cp(f"4{label}"), 4)
    return _Run(approved, effects[0], last)


def _resume(t: _Trial, worker: str, decision: str) -> tuple[str, str, str]:
    """Bring the thread to its interrupt, then send the resume; returns (op, logical op, head)."""
    head = _to_interrupt(t, worker)
    logical = f"{DEMO}{decision}-{t.trial}"
    op = _invoke(t, logical, worker, resume=decision)
    _read(t, worker, op, head)
    return op, logical, head


# -- stories: one scripted shape of trial per demonstration cell ----------------------------
#
# Invariant and clause ids are demo ids. The outcomes are typed in, not decided by a checker.

_DURABLE = f"{DEMO}clause-durable-ack"
_RERUN = f"{DEMO}clause-rerun"
_LATEST = f"{DEMO}clause-latest"
_WRITES = f"{DEMO}clause-pending-writes"
_LEASE = f"{DEMO}clause-lease"
_I1, _I3, _I4, _I5 = (f"{DEMO}I1", f"{DEMO}I3", f"{DEMO}I4", f"{DEMO}I5")
_I6, _I7, _I8, _I9, _I11 = (f"{DEMO}I6", f"{DEMO}I7", f"{DEMO}I8", f"{DEMO}I9", f"{DEMO}I11")
FINDING_REPRO = f"{DEMO}repro-W1-F08-orphaned-effect"


def _fault_free(t: _Trial) -> list[_Outcome]:
    """C1: one worker, no faults."""
    _start_workers(t, WORKER_A)
    op, logical, head = _resume(t, WORKER_A, "approve")
    run = _approve_run(t, WORKER_A, op, logical, head, attempt=1)
    _answer(t, op, run.last)
    _exit_workers(t, WORKER_A)
    return [
        _Outcome(_I1, "PASS", _DURABLE, (run.last,)),
        _Outcome(_I3, "PASS", _RERUN, (run.effect.append,)),
        _Outcome(_I7, "NO_ANOMALY_UNSPECIFIED"),
    ]


def _forced_fork(t: _Trial) -> list[_Outcome]:
    """C2: two new turns on one thread, both workers held after get_tuple and released together;
    each writes a child of the same checkpoint."""
    _start_workers(t, WORKER_A, WORKER_B)
    first = _invoke(t, f"{DEMO}turn-1-{t.trial}", WORKER_A)
    _read(t, WORKER_A, first, None)
    _answer(t, first, _node(t, WORKER_A, first, "respond", None, _cp(1), 1))
    ops = {w: _invoke(t, f"{DEMO}turn-2-{t.trial}-{w}", w) for w in (WORKER_A, WORKER_B)}
    hits: list[Event] = []
    for worker, op in ops.items():
        _read(t, worker, op, _cp(1))
        hits.append(_hit(t, worker, op, "get_tuple.after"))
    _release(t, *hits)
    puts: list[Event] = []
    for worker, label in ((WORKER_A, "a"), (WORKER_B, "b")):
        puts.append(_node(t, worker, ops[worker], "respond", _cp(1), _cp(f"2{label}"), 2))
        _answer(t, ops[worker], puts[-1])
    _exit_workers(t, WORKER_A, WORKER_B)
    return [
        _Outcome(_I1, "PASS", _DURABLE, tuple(puts)),
        _Outcome(_I5, "GAP", None, tuple(puts)),
        _Outcome(_I7, "NO_ANOMALY_UNSPECIFIED"),
    ]


def _conflicting_resume(t: _Trial) -> list[_Outcome]:
    """Approve on A and reject on B, released together: the lineage forks at the interrupt, B's
    branch is written last, and A's charge was produced on a branch nobody resumes."""
    _start_workers(t, WORKER_A, WORKER_B)
    head = _to_interrupt(t, WORKER_A)
    approve, reject = f"{DEMO}approve-{t.trial}", f"{DEMO}reject-{t.trial}"
    op_a = _invoke(t, approve, WORKER_A, resume="approve")
    op_b = _invoke(t, reject, WORKER_B, resume="reject")
    hits: list[Event] = []
    for worker, op in ((WORKER_A, op_a), (WORKER_B, op_b)):
        _read(t, worker, op, head)
        hits.append(_hit(t, worker, op, "get_tuple.after"))
    _release(t, *hits)
    run = _approve_run(t, WORKER_A, op_a, approve, head, attempt=1, label="a")
    rejected = _node(t, WORKER_B, op_b, "approval", head, _cp("2b"), 2)
    last_b = _node(t, WORKER_B, op_b, "record", _cp("2b"), _cp("3b"), 3)
    _answer(t, op_a, run.last)
    _answer(t, op_b, last_b)
    _exit_workers(t, WORKER_A, WORKER_B)
    orphan = (run.effect.request, run.effect.append, last_b)
    return [
        _Outcome(_I1, "PASS", _DURABLE, (run.last, last_b)),
        _Outcome(_I5, "GAP", None, (run.approved, rejected)),
        _Outcome(_I7, "GAP", None, orphan, repro=FINDING_REPRO),
        _Outcome(_I8, "GAP", None, (run.effect.request, rejected)),
    ]


def _kill_after_effect(t: _Trial) -> list[_Outcome]:
    """A is killed after its charge, before its writes are saved; the retry on B reruns the node
    and the charge happens twice, as the re-run clause permits."""
    _start_workers(t, WORKER_A, WORKER_B)
    op, logical, head = _resume(t, WORKER_A, "approve")
    approved = _node(t, WORKER_A, op, "approval", head, _cp(2), 2)
    key = f"{DEMO}charge-order-{t.trial}"
    _start(t, WORKER_A, op, "execute")
    first = _effect(t, WORKER_A, op, logical, key, 1, _cp(2), "execute", "approve")
    hit = _hit(t, WORKER_A, op, "put_writes.before")
    _fault(t, {"signal": "SIGKILL", "proc": WORKER_A}, {"fp": hit.fp, "worker": WORKER_A})
    t.emit(ORCH, "worker.exit", data={"proc": WORKER_A, "returncode": -9, "signal": "SIGKILL"})
    t.emit(ORCH, "client.info", op_id=op, status="info", error="worker died before its result")
    retry = _invoke(t, logical, WORKER_B, resume="approve")
    _read(t, WORKER_B, retry, approved.checkpoint_id)
    seconds: list[_Effect] = []

    def charge() -> None:
        seconds.append(_effect(t, WORKER_B, retry, logical, key, 2, _cp(2), "execute", "approve"))

    _node(t, WORKER_B, retry, "execute", _cp(2), _cp(3), 3, attempt=2, effect=charge)
    last = _node(t, WORKER_B, retry, "record", _cp(3), _cp(4), 4)
    _answer(t, retry, last)
    _exit_workers(t, WORKER_B)
    return [
        _Outcome(_I1, "PASS", _DURABLE, (last,)),
        _Outcome(_I3, "PASS", _RERUN, (seconds[0].append,)),
        _Outcome(_I4, "EXPECTED", _RERUN, (first.append, seconds[0].append)),
    ]


def _stop_continue(t: _Trial) -> list[_Outcome]:
    """A is frozen with SIGSTOP while parked at a failpoint, then continued; one writer."""
    _start_workers(t, WORKER_A)
    op, logical, head = _resume(t, WORKER_A, "approve")
    hit = _hit(t, WORKER_A, op, "put.before")
    stop = _fault(t, {"signal": "SIGSTOP", "proc": WORKER_A}, {"fp": hit.fp, "worker": WORKER_A})
    t.wait(300 * MS, 900 * MS)
    _heal(t, stop, {"signal": "SIGCONT", "proc": WORKER_A})
    _release(t, hit, trigger={"healed": stop})
    run = _approve_run(t, WORKER_A, op, logical, head, attempt=1)
    _answer(t, op, run.last)
    _exit_workers(t, WORKER_A)
    return [
        _Outcome(_I1, "PASS", _DURABLE, (run.last,)),
        _Outcome(_I3, "PASS", _RERUN, (run.effect.append,)),
        _Outcome(_I6, "PASS", _LATEST, (run.last,)),
    ]


def _lost_ack(t: _Trial) -> list[_Outcome]:
    """The database's answer to a put is held by a timeout toxic, then the connection is reset,
    so the client cannot know whether the checkpoint committed. The second trial's history ends
    without the client's terminal event, so that trial is INVALID."""
    _start_workers(t, WORKER_A)
    op, _, head = _resume(t, WORKER_A, "approve")
    hit = _hit(t, WORKER_A, op, "put.before")
    proxy = "pg-worker-a"
    held = _fault(
        t,
        {"toxic": "timeout", "stream": "downstream", "proxy": proxy, "attributes": {"timeout": 0}},
        {"fp": hit.fp, "worker": WORKER_A},
    )
    _release(t, hit)
    _start(t, WORKER_A, op, "approval")
    fields = _put_fields(t, op, _cp(2), head, 2)
    call = t.emit(WORKER_A, "saver.put.call", **fields)
    t.wait(400 * MS, 700 * MS)
    reset = _fault(
        t,
        {
            "toxic": "reset_peer",
            "stream": "downstream",
            "proxy": proxy,
            "attributes": {"timeout": 0},
        },
        {"after": held},
    )
    _heal(t, held, {"toxic": "timeout", "proxy": proxy, "removed": True})
    error = "OperationalError: server closed the connection unexpectedly"
    raised = t.emit(WORKER_A, "saver.put.raise", status="fail", error=error, **fields)
    _heal(t, reset, {"toxic": "reset_peer", "proxy": proxy, "removed": True})
    if t.trial == 1:
        reason = f"Completeness check failed: {op} has no terminal client event. {NOTE}"
        return [_Outcome(_I1, "INVALID", reason=reason), _Outcome(_I11, "INVALID", reason=reason)]
    _answer(t, op, raised, status="fail", error=error)
    _exit_workers(t, WORKER_A)
    return [
        _Outcome(_I1, "PASS", _DURABLE, (raised,)),
        _Outcome(_I11, "GAP", None, (call, raised)),
    ]


def _database_restart(t: _Trial) -> list[_Outcome]:
    """Postgres restarts while a put is in flight: the put fails, the client is told, and the
    retried run completes."""
    _start_workers(t, WORKER_A)
    op, logical, head = _resume(t, WORKER_A, "approve")
    hit = _hit(t, WORKER_A, op, "put.before")
    restart = _fault(
        t,
        {"action": "db-restart", "service": "postgres", "mode": "fast"},
        {"fp": hit.fp, "worker": WORKER_A},
    )
    _release(t, hit)
    _start(t, WORKER_A, op, "approval")
    fields = _put_fields(t, op, _cp(2), head, 2)
    t.emit(WORKER_A, "saver.put.call", **fields)
    error = "AdminShutdown: terminating connection due to administrator command"
    raised = t.emit(WORKER_A, "saver.put.raise", status="fail", error=error, **fields)
    t.wait(2 * SECOND, 4 * SECOND)
    _heal(t, restart, {"service": "postgres", "ready": True})
    _answer(t, op, raised, status="fail", error=error)
    retry = _invoke(t, logical, WORKER_A, resume="approve")
    _read(t, WORKER_A, retry, head)
    run = _approve_run(t, WORKER_A, retry, logical, head, attempt=1)
    _answer(t, retry, run.last)
    _exit_workers(t, WORKER_A)
    return [
        _Outcome(_I1, "PASS", _DURABLE, (run.last,)),
        _Outcome(_I3, "PASS", _RERUN, (run.effect.append,)),
        _Outcome(_I11, "PASS", _DURABLE, (run.last,)),
    ]


def _fan_out_latency(t: _Trial) -> list[_Outcome]:
    """W2: three children, each with an effect, under a latency toxic fired at a seeded time
    offset (a randomized cell). In the third trial one child's writes are saved twice."""
    _start_workers(t, WORKER_A)
    proxy = "pg-worker-a"
    latency = _fault(
        t,
        {
            "toxic": "latency",
            "stream": "downstream",
            "proxy": proxy,
            "attributes": {"latency": t.rng.between(20, 120), "jitter": t.rng.between(5, 40)},
        },
        {"offset_ns": t.rng.between(2 * MS, 40 * MS)},
    )
    logical = f"{DEMO}fan-out-{t.trial}"
    op = _invoke(t, logical, WORKER_A)
    _read(t, WORKER_A, op, None)
    _node(t, WORKER_A, op, "plan", None, _cp(1), 1)
    saved: list[Event] = []
    appends: list[Event] = []
    for child in (1, 2, 3):
        node = f"child-{child}"
        task = _start(t, WORKER_A, op, node)
        key = f"{DEMO}notify-{t.trial}-{child}"
        appends.append(_effect(t, WORKER_A, op, logical, key, 1, _cp(1), node).append)
        for _ in range(2 if (t.trial == 2 and child == 2) else 1):
            path = f"~__pregel_push, {child}"
            saved.append(_writes(t, WORKER_A, op, _cp(1), task, "results", path))
        t.emit(WORKER_A, "node.end", op_id=op, attempt=1, task_id=task, data={"node": node})
    _put(t, WORKER_A, op, _cp(2), _cp(1), 2)
    last = _node(t, WORKER_A, op, "join", _cp(2), _cp(3), 3)
    _answer(t, op, last)
    _heal(t, latency, {"toxic": "latency", "proxy": proxy, "removed": True})
    _exit_workers(t, WORKER_A)
    outcomes = [
        _Outcome(_I1, "PASS", _DURABLE, (last,)),
        _Outcome(_I3, "PASS", _RERUN, tuple(appends)),
    ]
    if t.trial == 2:
        outcomes.append(_Outcome(_I9, "VIOLATION_CANDIDATE", _WRITES, (saved[1], saved[2])))
    else:
        outcomes.append(_Outcome(_I9, "PASS", _WRITES, tuple(saved)))
    return outcomes


def _stale_worker(t: _Trial) -> list[_Outcome]:
    """A is frozen mid-run and its op is redelivered to B, which finishes; A is continued and
    writes from the state it had read. Runtime A saves the stale write; runtime B refuses it."""
    _start_workers(t, WORKER_A, WORKER_B)
    op, logical, head = _resume(t, WORKER_A, "approve")
    hit = _hit(t, WORKER_A, op, "put.before")
    stop = _fault(t, {"signal": "SIGSTOP", "proc": WORKER_A}, {"fp": hit.fp, "worker": WORKER_A})
    t.wait(800 * MS, 1_200 * MS)
    t.emit(ORCH, "dispatch.redeliver", op_id=op, logical_op=logical, data={"to": WORKER_B})
    _read(t, WORKER_B, op, head)
    run = _approve_run(t, WORKER_B, op, logical, head, attempt=1, label="b")
    _answer(t, op, run.last)
    _exit_workers(t, WORKER_B)
    _heal(t, stop, {"signal": "SIGCONT", "proc": WORKER_A})
    _release(t, hit, trigger={"healed": stop})
    _start(t, WORKER_A, op, "approval")
    fields = _put_fields(t, op, _cp("2a"), head, 2)
    stale = t.emit(WORKER_A, "saver.put.call", **fields)
    if t.cell.startswith(f"{DEMO}rt-b-"):
        error = "LeaseLost: the thread is owned by another worker"
        refused = t.emit(WORKER_A, "saver.put.raise", status="fail", error=error, **fields)
        _exit_workers(t, WORKER_A)
        return [
            _Outcome(_I1, "PASS", _DURABLE, (run.last,)),
            _Outcome(_I6, "PASS", _LEASE, (stale, refused)),
        ]
    saved = t.emit(WORKER_A, "saver.put.return", status="ok", **fields)
    _exit_workers(t, WORKER_A)
    return [
        _Outcome(_I1, "PASS", _DURABLE, (run.last,)),
        _Outcome(_I6, "GAP", None, (stale, saved)),
    ]


_STORIES: dict[str, Callable[[_Trial], list[_Outcome]]] = {
    "fault-free": _fault_free,
    "forced-fork": _forced_fork,
    "conflicting-resume": _conflicting_resume,
    "kill-after-effect": _kill_after_effect,
    "stop-continue": _stop_continue,
    "lost-ack": _lost_ack,
    "database-restart": _database_restart,
    "fan-out-latency": _fan_out_latency,
    "stale-worker": _stale_worker,
}


# -- batches -----------------------------------------------------------------------------------


def _manifest(batch: DemoBatch) -> Manifest:
    started = datetime.fromtimestamp((WALL_BASE + batch.start_ns) // SECOND - 60, tz=UTC)
    return Manifest(
        generated_at=started.isoformat(),
        git_sha=f"{DEMO}git-sha",
        git_dirty=False,
        python_version="3.12.13",
        python_implementation="CPython",
        os_system="Linux",
        os_release="6.8.0",
        machine="x86_64",
        runner="local",
        lockfile_sha256=f"{DEMO}lockfile-sha256",
        clause_registry_sha256=f"{DEMO}clause-registry-sha256",
        spec_sha256=f"{DEMO}spec-sha256",
        packages=dict(batch.packages),
        psycopg_impl="binary",
        psycopg_wheel="cp312-cp312-manylinux_2_17_x86_64",
        libpq_version="18.4",
        libpq_build_version="18.4",
        docker_version="28.4.0",
        docker_kernel="6.8.0",
        compose_version="2.39.4",
        images={"postgres": f"{DEMO}image-postgres", "toxiproxy": f"{DEMO}image-toxiproxy"},
        image_ids={
            "postgres": f"{DEMO}image-id-postgres",
            "toxiproxy": f"{DEMO}image-id-toxiproxy",
        },
        postgres={
            "default_transaction_isolation": "read committed",
            "fsync": "on",
            "max_connections": "100",
            "server_version": "18.6",
            "synchronous_commit": "on",
            "track_commit_timestamp": "on",
        },
        toxiproxy_version="2.12.0",
        sink_clock_offset_ns=0,
    )


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _lines(events: tuple[Event, ...] | list[Event]) -> bytes:
    return "".join(e.to_line() for e in events).encode()


def _batch_files(batch: DemoBatch, master: int) -> dict[PurePosixPath, bytes]:
    root = PurePosixPath(batch.batch)
    manifest = _manifest(batch).to_json().encode()
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    record = BatchRecord(
        v=1,
        batch=batch.batch,
        provenance="fixture",
        manifest_sha256=manifest_sha,
        scenario_sha256=scenario_sha256(batch.scenario),
    )
    files = {root / "manifest.json": manifest, root / "batch.json": _json(record.model_dump())}
    rng = _Rng(master, batch.batch)
    mono = MONO_BASE + batch.start_ns + rng.between(0, SECOND)
    orchestrator, sink = rng.between(40_000, 48_000), rng.between(7, 60)
    for cell in batch.scenario.cells:
        cid = cell_id(batch, cell)
        for trial in range(cell.trials):
            pids = {
                ORCH: orchestrator,
                SINK: sink,
                WORKER_A: rng.between(50_000, 90_000),
                WORKER_B: rng.between(50_000, 90_000),
            }
            t = _Trial(
                batch=batch.batch,
                cell=cid,
                trial=trial,
                seed=trial_seed(master, cid, trial),
                rng=_Rng(master, cid, trial),
                mono=mono,
                pids=pids,
                thread=f"{DEMO}thread-{trial}",
            )
            outcomes = _STORIES[cell.story](t)
            merged = merge(t.histories.values())
            index = {(e.proc, e.seq): i for i, e in enumerate(merged)}
            folder = root / cid / str(trial)
            for proc, events in sorted(t.histories.items()):
                files[folder / f"{proc}.jsonl"] = _lines(events)
            files[folder / "merged.jsonl"] = _lines(merged)
            results = [
                InvariantResult.model_validate(
                    {
                        "invariant": o.invariant,
                        "outcome": o.outcome,
                        "clause": o.clause,
                        "evidence": [
                            f"{cid}/{trial}/merged.jsonl#/{index[(e.proc, e.seq)]}"
                            for e in o.evidence
                        ],
                        "repro": o.repro,
                        "reason": o.reason or NOTE,
                    }
                )
                for o in outcomes
            ]
            result = ResultRecord(
                v=1,
                batch=batch.batch,
                cell=cid,
                trial=trial,
                provenance="fixture",
                manifest_sha256=manifest_sha,
                results=tuple(results),
            )
            files[folder / "result.json"] = _json(result.model_dump(mode="json"))
            mono = t.mono + rng.between(200 * MS, 1_500 * MS)
    return files


def generate(seed: int = DEFAULT_SEED) -> dict[PurePosixPath, bytes]:
    """Every fixture file, by path relative to the fixture directory, in path order."""
    files: dict[PurePosixPath, bytes] = {}
    for batch in BATCHES:
        files.update(_batch_files(batch, seed))
    return dict(sorted(files.items()))


class FixtureDirError(ValueError):
    """The output directory holds something other than fixtures, or is a results directory."""


def _check_out(out: Path) -> None:
    if out.resolve().name == "results":
        raise FixtureDirError(f"{out}: fixtures never go in a results directory")
    if out.exists():
        foreign = sorted(p.name for p in out.iterdir() if not p.name.startswith(DEMO))
        if foreign:
            raise FixtureDirError(
                f"{out} holds entries that are not fixtures: {', '.join(foreign)}"
            )


def write(out: Path, files: Mapping[PurePosixPath, bytes]) -> None:
    """Replace the fixtures in `out` with `files`; refuses a directory holding anything else."""
    _check_out(out)
    if out.exists():
        for entry in sorted(out.rglob("*"), reverse=True):  # children before their folders
            if entry.is_dir():
                entry.rmdir()
            else:
                entry.unlink()
    for rel, data in files.items():
        path = out.joinpath(*rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def differences(out: Path, files: Mapping[PurePosixPath, bytes]) -> list[str]:
    """How `out` differs from `files`; empty means the committed fixtures are current."""
    present: set[PurePosixPath] = set()
    if out.exists():
        present = {
            PurePosixPath(p.relative_to(out).as_posix()) for p in out.rglob("*") if p.is_file()
        }
    problems = [f"{rel} is not a generated fixture" for rel in sorted(present - files.keys())]
    for rel, data in files.items():
        if rel not in present:
            problems.append(f"{rel} is missing")
        elif out.joinpath(*rel.parts).read_bytes() != data:
            problems.append(f"{rel} differs from the generator's output")
    return problems
