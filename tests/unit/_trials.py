"""Synthetic trials for the checker: histories, ledger, and snapshot built together.

Synthetic histories are fixtures (provenance "fixture", DEMO- batch ids). They follow the event
order of langgraph 1.2.12 as the source reads (a Command's resume value is written by the null
task, then the interrupted node re-runs and writes its own RESUME list; a task's writes go to the
saver after its node returns) so that the checker's predicates meet realistic shapes. They are
not evidence, and nothing in them is a landmark: occurrence numbers here exist only to make a
fixture's arms match its own hits.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from crashcourt.checker.artifacts import (
    ArmSpec,
    BlobRow,
    CheckpointRow,
    FaultSpec,
    LandmarkTable,
    LedgerExport,
    LedgerRow,
    OpSpec,
    Point,
    ProcessHistory,
    SinkHealth,
    Snapshot,
    SnapshotMeta,
    SnapshotPair,
    TrialArtifacts,
    TrialScenario,
    WriteRow,
)
from crashcourt.checker.decode import encode
from crashcourt.checker.facts import NULL_TASK_ID
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256
from crashcourt.spec.events import Event
from tests.unit._factories import GOOD_VERSIONS, make_manifest

REPO = Path(__file__).resolve().parents[2]
CLAUSES = REPO / "clauses"
DSN = "postgresql://oracle@127.0.0.1:55432/crashcourt"
SINK_SOURCE = "a" * 64
PACKAGES = {**GOOD_VERSIONS, "langgraph": "1.2.12", "langgraph-checkpoint": "4.2.0"}
MANIFEST = make_manifest(packages=PACKAGES)
MANIFEST_SHA = hashlib.sha256(MANIFEST.to_json().encode()).hexdigest()
SPECIAL = {"__interrupt__": -3, "__resume__": -4, "__error__": -1}


def registry() -> tuple[ClauseRegistry, str]:
    return ClauseRegistry.load(CLAUSES), registry_sha256(CLAUSES)


@dataclass
class Proc:
    name: str
    pid: int
    batch: str
    cell: str
    trial: int
    clock: list[int]
    events: list[Event] = field(default_factory=list[Event])

    def emit(self, kind: str, **fields: Any) -> Event:
        self.clock[0] += 1_000
        event = Event.model_validate(
            {
                "v": 1,
                "batch": self.batch,
                "cell": self.cell,
                "trial": self.trial,
                "seed": "0",
                "proc": self.name,
                "pid": self.pid,
                "seq": len(self.events),
                "mono_ns": self.clock[0],
                "wall_ns": 1_800_000_000_000_000_000 + self.clock[0],
                "kind": kind,
                **fields,
            }
        )
        self.events.append(event)
        return event

    @property
    def last_seq(self) -> int:
        return len(self.events) - 1


@dataclass
class ThreadGraph:
    """The saver's view of one thread: current values and versions, and the head."""

    values: dict[str, Any] = field(default_factory=dict[str, Any])
    versions: dict[str, str] = field(default_factory=dict[str, str])
    head: str | None = None
    step: int = -2


def _version(n: int) -> str:
    return f"{n:032}.{0:016}"


class TrialBuilder:
    def __init__(
        self,
        *,
        workload: str = "w1",
        durability: str = "sync",
        threads: tuple[str, ...] = ("T1",),
        evidence: str = "synthetic",
        batch: str = "DEMO-t10",
        cell: str = "DEMO-cell",
        trial: int = 0,
    ) -> None:
        self.workload = workload
        self.durability = durability
        self.threads = threads
        self.evidence = evidence
        self.batch, self.cell, self.trial = batch, cell, trial
        self.clock = [0]
        self.orch = self._proc("orchestrator", 100)
        self.sink = self._proc("sink", 200)
        self.workers: dict[str, Proc] = {}
        self.ops: list[OpSpec] = []
        self.arms: list[ArmSpec] = []
        self.faults: list[FaultSpec] = []
        self.landmarks: LandmarkTable | None = None
        self.graphs: dict[str, ThreadGraph] = {t: ThreadGraph() for t in threads}
        self.checkpoints: dict[tuple[str, str], CheckpointRow] = {}
        self.blobs: dict[tuple[str, str, str], BlobRow] = {}
        self.writes: dict[tuple[str, str, str, int], WriteRow] = {}
        self.ledger: list[LedgerRow] = []
        self.attempts: dict[str, int] = {}
        self.ids = 0
        self.xmin = 1000
        self.version_counter = 0
        self.hit_counts: dict[tuple[str, str, str | None], int] = {}
        self.pending: dict[str, tuple[Any, ...]] = {}

    # -- processes --------------------------------------------------------------------------

    def _proc(self, name: str, pid: int) -> Proc:
        return Proc(name, pid, self.batch, self.cell, self.trial, self.clock)

    def worker(self, name: str) -> Proc:
        if name not in self.workers:
            self.workers[name] = self._proc(name, 300 + len(self.workers))
            self.orch.emit("worker.start", data={"proc": name})
        return self.workers[name]

    # -- orchestrator -------------------------------------------------------------------------

    def op(self, op_id: str, kind: str, thread: str = "T1", resume: str | None = None) -> OpSpec:
        spec = OpSpec(
            op_id=op_id,
            kind=kind,  # pyright: ignore[reportArgumentType]
            thread_id=thread,
            logical_op=f"{kind}:{thread}:{resume or op_id}"
            if kind != "resume"
            else f"resume:{thread}",
            resume=resume,
        )
        self.ops.append(spec)
        return spec

    def invoke(self, spec: OpSpec, *, logical_op: str | None = None) -> Event:
        data: dict[str, Any] = {"resume": spec.resume} if spec.resume is not None else {}
        return self.orch.emit(
            "client.invoke", op_id=spec.op_id, logical_op=logical_op or spec.logical_op, data=data
        )

    def dispatch(self, op_id: str, to: str, *, redeliver: bool = False) -> Event:
        self.worker(to)
        kind = "dispatch.redeliver" if redeliver else "dispatch.send"
        return self.orch.emit(kind, op_id=op_id, data={"to": to})

    def ok(self, op_id: str, worker: str) -> Event:
        seq = self.workers[worker].last_seq
        return self.orch.emit(
            "client.ok", op_id=op_id, status="ok", data={"worker": worker, "worker_seq": seq}
        )

    def fail(self, op_id: str, worker: str, error: str = "error") -> Event:
        seq = self.workers[worker].last_seq
        return self.orch.emit(
            "client.fail",
            op_id=op_id,
            status="fail",
            error=error,
            data={"worker": worker, "worker_seq": seq},
        )

    def info(self, op_id: str, error: str = "no result") -> Event:
        return self.orch.emit("client.info", op_id=op_id, status="info", error=error)

    def exit(self, worker: str, *, returncode: int = 0, signal: str | None = None) -> Event:
        data: dict[str, Any] = {"proc": worker, "returncode": returncode}
        if signal is not None:
            data["signal"] = signal
        return self.orch.emit("worker.exit", data=data)

    def inject(self, fault_id: str, **data: Any) -> Event:
        return self.orch.emit("fault.inject", fault_id=fault_id, data=data)

    def heal(self, fault_id: str, *, status: str = "ok", **data: Any) -> Event:
        return self.orch.emit("fault.heal", fault_id=fault_id, status=status, data=data)

    # -- the saver ---------------------------------------------------------------------------

    def _new_id(self) -> str:
        self.ids += 1
        return f"1f0a{self.ids:04x}-0000-6000-8000-{self.ids:012x}"

    def _commit(self) -> tuple[str, int]:
        self.xmin += 1
        return str(self.xmin), 1_800_000_000_000_000_000 + self.clock[0] + 1

    def get_tuple(self, w: Proc, op: str, thread: str) -> Event:
        head = self.graphs[thread].head
        w.emit("saver.get_tuple.call", op_id=op, thread_id=thread, checkpoint_ns="")
        if head is None:
            data: dict[str, Any] = {"found": False, "requested": None}
            return w.emit(
                "saver.get_tuple.return",
                op_id=op,
                thread_id=thread,
                checkpoint_ns="",
                status="ok",
                data=data,
            )
        parent = self.checkpoints[(thread, head)].parent_checkpoint_id
        return w.emit(
            "saver.get_tuple.return",
            op_id=op,
            thread_id=thread,
            checkpoint_ns="",
            checkpoint_id=head,
            parent_checkpoint_id=parent,
            status="ok",
            data={"found": True, "requested": None, "pending_writes": 0},
        )

    def fp(self, w: Proc, point: str, op: str, action: str, **ids: Any) -> Event:
        key = (w.name, point, op)
        self.hit_counts[key] = self.hit_counts.get(key, 0) + 1
        hit = w.emit(
            "fp.hit",
            op_id=op,
            fp=point,
            data={"action": action, "occurrence": self.hit_counts[key]},
            **ids,
        )
        return hit

    def act(self, w: Proc, hit: Event, *, signal: str | None = None) -> Event:
        data: dict[str, Any] = {"hit_seq": hit.seq, "action": hit.data["action"]}
        if signal is not None:
            data["signal"] = signal
        return w.emit("fp.action", fp=hit.fp, thread_id=hit.thread_id, data=data)

    def put(
        self,
        w: Proc,
        op: str,
        thread: str,
        *,
        source: str,
        updates: dict[str, Any],
        parent: str | None = None,
        commit: bool = True,
        returns: bool = True,
        stop_before: str | None = None,
    ) -> str:
        """saver.put of the next checkpoint. `stop_before` names a signal to die with at
        put.before (the call is logged, the hit and action too, then nothing)."""
        graph = self.graphs[thread]
        cp = self._new_id()
        parent = graph.head if parent is None else parent
        step = graph.step + 1
        for channel in updates:
            self.version_counter += 1
            graph.versions[channel] = _version(self.version_counter)
        graph.values.update(updates)
        fields = {
            "op_id": op,
            "thread_id": thread,
            "checkpoint_ns": "",
            "checkpoint_id": cp,
            "parent_checkpoint_id": parent,
        }
        w.emit(
            "saver.put.call",
            **fields,
            data={"step": step, "source": source, "channels": sorted(updates)},
        )
        if stop_before is not None:
            hit = self.fp(
                w, "put.before", op, "signal", **{k: v for k, v in fields.items() if k != "op_id"}
            )
            self.act(w, hit, signal=stop_before)
            self.pending[cp] = (
                thread,
                source,
                step,
                parent,
                dict(updates),
                dict(graph.values),
                dict(graph.versions),
            )
            return cp
        if commit:
            self._store(thread, cp, parent, source, step, updates, graph.values, graph.versions)
        if returns:
            w.emit("saver.put.return", **fields, status="ok", data={})
        graph.head = cp
        graph.step = step
        return cp

    def finish_put(self, w: Proc, op: str, cp: str) -> None:
        """A put stopped at put.before goes on (after SIGCONT): it commits and returns."""
        thread, source, step, parent, updates, values, versions = self.pending.pop(cp)
        self._store(thread, cp, parent, source, step, updates, values, versions)
        w.emit(
            "saver.put.return",
            op_id=op,
            thread_id=thread,
            checkpoint_ns="",
            checkpoint_id=cp,
            parent_checkpoint_id=parent,
            status="ok",
            data={},
        )

    def _store(
        self,
        thread: str,
        cp: str,
        parent: str | None,
        source: str,
        step: int,
        updates: dict[str, Any],
        values: dict[str, Any],
        versions: dict[str, str],
    ) -> None:
        """The rows a committed put leaves: blobs for non-primitive new values, the checkpoint."""
        xmin, commit_ns = self._commit()
        inline = {
            k: v for k, v in values.items() if v is None or isinstance(v, str | int | float | bool)
        }
        for channel in updates:
            if channel not in inline:
                type_, blob = encode(values[channel])
                self.blobs[(thread, channel, versions[channel])] = BlobRow(
                    thread_id=thread,
                    checkpoint_ns="",
                    channel=channel,
                    version=versions[channel],
                    type=type_,
                    blob=blob,
                    xmin=xmin,
                    commit_ns=commit_ns,
                )
        self.checkpoints[(thread, cp)] = CheckpointRow(
            thread_id=thread,
            checkpoint_ns="",
            checkpoint_id=cp,
            parent_checkpoint_id=parent,
            checkpoint=cast(
                "dict[str, JsonValue]",
                {"id": cp, "channel_values": inline, "channel_versions": dict(versions)},
            ),
            metadata={"source": source, "step": step},
            xmin=xmin,
            commit_ns=commit_ns,
        )

    def put_writes(
        self,
        w: Proc,
        op: str,
        thread: str,
        cp: str,
        task: str,
        writes: list[tuple[str, Any]],
        *,
        commit: bool = True,
        returns: bool = True,
        stop: tuple[str, str] | None = None,
    ) -> None:
        """saver.put_writes. `stop` = (point, signal): die at put_writes.before or .after."""
        fields = {
            "op_id": op,
            "thread_id": thread,
            "checkpoint_ns": "",
            "checkpoint_id": cp,
            "task_id": task,
        }
        w.emit(
            "saver.put_writes.call",
            **fields,
            data={"channels": [c for c, _ in writes], "task_path": ""},
        )
        ids = {k: v for k, v in fields.items() if k != "op_id"}
        if stop is not None and stop[0] == "put_writes.before":
            self.act(w, self.fp(w, "put_writes.before", op, "signal", **ids), signal=stop[1])
            return
        if commit:
            self.commit_writes(thread, cp, task, writes)
        if stop is not None and stop[0] == "put_writes.after":
            self.act(w, self.fp(w, "put_writes.after", op, "signal", **ids), signal=stop[1])
            return
        if returns:
            w.emit("saver.put_writes.return", **fields, status="ok", data={})

    def commit_writes(self, thread: str, cp: str, task: str, writes: list[tuple[str, Any]]) -> None:
        """The checkpoint_writes rows a committed put_writes leaves. As the Postgres saver does,
        a batch of special writes upserts; any other batch inserts and keeps an existing row."""
        xmin, commit_ns = self._commit()
        upsert = all(channel in SPECIAL for channel, _ in writes)
        for idx, (channel, value) in enumerate(writes):
            type_, blob = encode(value)
            index = SPECIAL.get(channel, idx)
            if not upsert and (thread, cp, task, index) in self.writes:
                continue
            self.writes[(thread, cp, task, index)] = WriteRow(
                thread_id=thread,
                checkpoint_ns="",
                checkpoint_id=cp,
                task_id=task,
                task_path="",
                idx=index,
                channel=channel,
                type=type_,
                blob=blob,
                xmin=xmin,
                commit_ns=commit_ns,
            )

    def node(self, w: Proc, op: str, name: str, task: str, *, end: bool = True) -> None:
        w.emit("node.start", op_id=op, task_id=task, attempt=1, data={"node": name})
        if end:
            self.node_end(w, op, name, task)

    def node_end(self, w: Proc, op: str, name: str, task: str) -> None:
        w.emit("node.end", op_id=op, task_id=task, attempt=1, data={"node": name})

    def effect(
        self, w: Proc, op: str, thread: str, node: str, key: str, *, decision: str | None = None
    ) -> int:
        """One effect: the worker's request, the ledger row and the sink's append, the response."""
        attempt = self.attempts.get(key, 0) + 1
        self.attempts[key] = attempt
        logical = next(o.logical_op for o in self.ops if o.op_id == op)
        data: dict[str, Any] = {"decision": decision} if decision is not None else {}
        w.emit(
            "effect.request",
            op_id=op,
            logical_op=logical,
            attempt=attempt,
            effect_key=key,
            thread_id=thread,
            data=data,
        )
        seq = len(self.ledger) + 1
        prev = self.ledger[-1].row_hash if self.ledger else "0" * 64
        body = {
            "sink_seq": seq,
            "effect_key": key,
            "op_id": op,
            "attempt": attempt,
            "worker": w.name,
        }
        row_hash = hashlib.sha256(f"{prev}|{json.dumps(body, sort_keys=True)}".encode()).hexdigest()
        self.ledger.append(
            LedgerRow(
                sink_seq=seq,
                received_mono_ns=self.clock[0],
                received_wall_ns=self.clock[0],
                effect_key=key,
                logical_op=logical,
                op_id=op,
                attempt=attempt,
                worker=w.name,
                pid=w.pid,
                thread_id=thread,
                node=node,
                checkpoint_id_seen=self.graphs[thread].head,
                payload_hash="b" * 64,
                status="applied",
                prev_hash=prev,
                row_hash=row_hash,
            )
        )
        self.sink.emit(
            "sink.append",
            op_id=op,
            logical_op=logical,
            attempt=attempt,
            effect_key=key,
            thread_id=thread,
            status="ok",
            data={"worker": w.name, "sink_seq": seq, "row_hash": row_hash},
        )
        w.emit(
            "effect.response",
            op_id=op,
            attempt=attempt,
            effect_key=key,
            thread_id=thread,
            status="ok",
            data={"sink_seq": seq},
        )
        return attempt

    # -- W1 -----------------------------------------------------------------------------------

    def task(self, thread: str, name: str) -> str:
        return f"task-{name}-{thread}"

    def w1_start(
        self,
        worker: str,
        op: str,
        thread: str = "T1",
        *,
        misuse: bool = False,
        kill_input: str | None = None,
    ) -> None:
        """start-1: input checkpoint, plan, the checkpoint after plan, approval interrupts."""
        w = self.workers[worker]
        self.get_tuple(w, op, thread)
        c0 = self.put(
            w,
            op,
            thread,
            source="input",
            updates={"__start__": {"order": thread}},
            stop_before=kill_input,
        )
        if kill_input is not None:
            return
        plan = self.task(thread, "plan")
        self.node(w, op, "plan", plan)
        self.put_writes(w, op, thread, c0, plan, [("plan", "p1")])
        c1 = self.put(w, op, thread, source="loop", updates={"plan": "p1"})
        approval = self.task(thread, "approval")
        self.node(w, op, "approval", approval, end=False)
        if misuse:
            self.effect(w, op, thread, "approval", f"precheck:{thread}")
        self.put_writes(w, op, thread, c1, approval, [("__interrupt__", ["approve?"])])

    def w1_approve(
        self, worker: str, op: str, thread: str, decision: str, *, misuse: bool = False
    ) -> str:
        """resume: the null task's RESUME write, approval re-runs, the checkpoint after approval."""
        w = self.workers[worker]
        self.get_tuple(w, op, thread)
        c1 = str(self.graphs[thread].head)
        self.put_writes(w, op, thread, c1, NULL_TASK_ID, [("__resume__", decision)])
        approval = self.task(thread, "approval")
        self.node(w, op, "approval", approval, end=False)
        if misuse:
            self.effect(w, op, thread, "approval", f"precheck:{thread}")
        self.node_end(w, op, "approval", approval)
        self.put_writes(
            w, op, thread, c1, approval, [("__resume__", [decision]), ("decision", decision)]
        )
        return self.put(w, op, thread, source="loop", updates={"decision": decision})

    def w1_execute(
        self,
        worker: str,
        op: str,
        thread: str,
        decision: str,
        *,
        stop: tuple[str, str] | None = None,
        end: bool = True,
    ) -> tuple[str, int | None]:
        """execute: the effect on approve, then its writes. Returns (checkpoint, attempt)."""
        w = self.workers[worker]
        c2 = str(self.graphs[thread].head)
        execute = self.task(thread, "execute")
        self.node(w, op, "execute", execute, end=False)
        attempt = None
        if decision == "approve":
            attempt = self.effect(w, op, thread, "execute", f"charge:{thread}", decision=decision)
        if not end:
            return c2, attempt
        self.node_end(w, op, "execute", execute)
        receipt = [f"charge:{thread}", attempt] if attempt is not None else "none"
        self.put_writes(w, op, thread, c2, execute, [("receipt", receipt)], stop=stop)
        return c2, attempt

    def w1_finish(
        self, worker: str, op: str, thread: str, executed: list[list[Any]], *, receipt: Any = None
    ) -> None:
        """The checkpoint after execute, record, and the final checkpoint."""
        w = self.workers[worker]
        self.put(
            w,
            op,
            thread,
            source="loop",
            updates={"receipt": receipt if receipt is not None else "none"},
        )
        c3 = str(self.graphs[thread].head)
        record = self.task(thread, "record")
        self.node(w, op, "record", record)
        self.put_writes(w, op, thread, c3, record, [("executed", executed)])
        self.put(w, op, thread, source="loop", updates={"executed": executed})

    def w1_resume(
        self, worker: str, op: str, thread: str, decision: str, *, misuse: bool = False
    ) -> None:
        self.w1_approve(worker, op, thread, decision, misuse=misuse)
        _, attempt = self.w1_execute(worker, op, thread, decision)
        executed = [[f"charge:{thread}", attempt]] if attempt is not None else []
        receipt = [f"charge:{thread}", attempt] if attempt is not None else "none"
        self.w1_finish(worker, op, thread, executed, receipt=receipt)

    # -- W5 -----------------------------------------------------------------------------------

    def w5_turn(
        self,
        worker: str,
        op: str,
        thread: str,
        turn: int,
        *,
        read: bool = True,
        parent: str | None = None,
        stop_put: str | None = None,
    ) -> str | None:
        w = self.workers[worker]
        if read:
            self.get_tuple(w, op, thread)
        messages = [*self.graphs[thread].values.get("messages", []), f"human {turn}"]
        cin = self.put(
            w,
            op,
            thread,
            source="input",
            updates={"__start__": {"messages": [f"human {turn}"]}},
            parent=parent,
        )
        task = f"task-model-{thread}-{turn}"
        self.node(w, op, "model", task)
        reply = [*messages, f"ai {turn} [attempt 1]"]
        self.put_writes(w, op, thread, cin, task, [("messages", reply)])
        if stop_put is not None:
            self.put(
                w, op, thread, source="loop", updates={"messages": reply}, stop_before=stop_put
            )
            return None
        return self.put(w, op, thread, source="loop", updates={"messages": reply})

    # -- the artifacts ------------------------------------------------------------------------

    def scenario(self, **changes: Any) -> TrialScenario:
        base: dict[str, Any] = {
            "batch": self.batch,
            "cell": self.cell,
            "trial": self.trial,
            "provenance": "fixture",
            "manifest_sha256": MANIFEST_SHA,
            "evidence": self.evidence,
            "workload": self.workload,
            "durability": self.durability,
            "target": "AsyncPostgresSaver",
            "threads": self.threads,
            "ops": tuple(self.ops),
            "arms": tuple(self.arms),
            "faults": tuple(self.faults),
            "landmarks": self.landmarks,
            "oracle_dsn": DSN,
            "runner": MANIFEST.runner,
            "sink_source_sha256": SINK_SOURCE,
        }
        base.update(changes)
        return TrialScenario.model_validate(base)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            meta=SnapshotMeta(
                after_orchestrator_seq=self.orch.last_seq,
                dsn=DSN,
                settings={
                    "fsync": "on",
                    "synchronous_commit": "on",
                    "track_commit_timestamp": "on",
                },
                postmaster_start_ns=0,
                threads=self.threads,
            ),
            checkpoints=tuple(self.checkpoints[k] for k in sorted(self.checkpoints)),
            blobs=tuple(self.blobs[k] for k in sorted(self.blobs)),
            writes=tuple(self.writes[k] for k in sorted(self.writes)),
        )

    def build(self, **scenario_changes: Any) -> TrialArtifacts:
        histories = [
            ProcessHistory(proc=p.name, events=tuple(p.events))
            for p in (self.orch, *self.workers.values())
        ]
        if self.sink.events:
            histories.append(ProcessHistory(proc="sink", events=tuple(self.sink.events)))
        snap = self.snapshot()
        return TrialArtifacts(
            scenario=self.scenario(**scenario_changes),
            manifest=MANIFEST,
            manifest_sha256=MANIFEST_SHA,
            histories=tuple(histories),
            ledger=LedgerExport(rows=tuple(self.ledger), verify_ok=True),
            sink_health=(
                SinkHealth(
                    when="after",
                    hooks_enabled=False,
                    source_sha256=SINK_SOURCE,
                    open_trial=f"{self.batch}/{self.cell}/{self.trial}",
                ),
            ),
            snapshots=SnapshotPair(
                first=snap, second=snap, pretrial_rows={t: 0 for t in self.threads}
            ),
        )


# -- canonical fixtures ------------------------------------------------------------------------


def w1_trial(
    decision: str = "approve", *, durability: str = "sync", misuse: bool = False
) -> TrialArtifacts:
    """C1 shape: one worker, no faults, start then one resume."""
    t = TrialBuilder(workload="w1-misuse" if misuse else "w1", durability=durability)
    start, resume = t.op("start-1", "start"), t.op("resume-1", "resume", resume=decision)
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1", misuse=misuse)
    t.ok("start-1", "worker-1")
    t.invoke(resume)
    t.dispatch("resume-1", "worker-1")
    t.w1_resume("worker-1", "resume-1", "T1", decision, misuse=misuse)
    t.ok("resume-1", "worker-1")
    t.exit("worker-1")
    return t.build()


def w5_fork_trial() -> TrialArtifacts:
    """C2 shape: turn 1 on one worker, then two workers paused after get_tuple and released
    together, each writing a child of the same head."""
    t = TrialBuilder(workload="w5")
    ops = [t.op("turn-1", "turn"), t.op("turn-2", "turn"), t.op("turn-3", "turn")]
    t.invoke(ops[0])
    t.dispatch("turn-1", "worker-1")
    t.w5_turn("worker-1", "turn-1", "T1", 1)
    t.ok("turn-1", "worker-1")
    graph = t.graphs["T1"]
    head, values, versions, step = graph.head, dict(graph.values), dict(graph.versions), graph.step
    hits = []
    for op, worker in (("turn-2", "worker-1"), ("turn-3", "worker-2")):
        t.invoke(ops[int(op[-1]) - 1])
        t.dispatch(op, worker)
        w = t.workers[worker]
        t.get_tuple(w, op, "T1")
        hits.append((w, t.fp(w, "get_tuple.after", op, "pause", thread_id="T1", checkpoint_ns="")))
    t.arms.extend(
        ArmSpec(point="get_tuple.after", action="pause", worker=w.name, op_id=op)
        for (w, _), op in zip(hits, ("turn-2", "turn-3"), strict=True)
    )
    for w, hit in hits:
        t.orch.emit("fp.release", data={"proc": w.name, "hit_seq": hit.seq})
    t.faults.extend(FaultSpec(kind="pause", arm=i) for i in range(2))
    for (w, hit), op, turn in zip(hits, ("turn-2", "turn-3"), (2, 3), strict=True):
        t.act(w, hit)
        graph.head, graph.values, graph.versions, graph.step = (
            head,
            dict(values),
            dict(versions),
            step,
        )
        t.w5_turn(w.name, op, "T1", turn, read=False, parent=head)
    t.ok("turn-2", "worker-1")
    t.ok("turn-3", "worker-2")
    t.exit("worker-1")
    t.exit("worker-2")
    return t.build()


def kill_mid_execute_trial(*, evidence: str = "synthetic") -> TrialArtifacts:
    """AM7's shape: SIGKILL while execute is running (after its effect, before node.end), the
    resume redelivered to a replacement, which runs execute again (attempt 2)."""
    t = TrialBuilder(evidence=evidence)
    start, resume = t.op("start-1", "start"), t.op("resume-1", "resume", resume="approve")
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1")
    t.ok("start-1", "worker-1")
    t.invoke(resume)
    t.dispatch("resume-1", "worker-1")
    t.w1_approve("worker-1", "resume-1", "T1", "approve")
    t.w1_execute("worker-1", "resume-1", "T1", "approve", end=False)
    t.inject("kill-1", fault="kill", signal="SIGKILL", proc="worker-1")
    t.exit("worker-1", returncode=-9, signal="SIGKILL")
    t.heal("kill-1", returncode=-9)
    t.dispatch("resume-1", "worker-2", redeliver=True)
    t.get_tuple(t.workers["worker-2"], "resume-1", "T1")
    _, attempt = t.w1_execute("worker-2", "resume-1", "T1", "approve")
    entry = ["charge:T1", attempt]
    t.w1_finish("worker-2", "resume-1", "T1", [entry], receipt=entry)
    t.ok("resume-1", "worker-2")
    t.exit("worker-2")
    t.faults.extend(
        [
            FaultSpec(kind="sigkill", fault_id="kill-1", worker="worker-1"),
            FaultSpec(kind="redelivery", op_ids=("resume-1",), worker="worker-2"),
        ]
    )
    return t.build()


def kill_at_writes_trial(point: Point, *, rerun: bool = True) -> TrialArtifacts:
    """SIGKILL at execute's put_writes failpoint (after its node.end), the resume redelivered to a
    replacement. With put_writes.before the writes never committed (the K1a / C11-8039 shape);
    with put_writes.after they did (the K1b shape). `rerun`: the replacement runs execute again;
    otherwise it reuses the committed writes and goes on to the next checkpoint."""
    t = TrialBuilder()
    start, resume = t.op("start-1", "start"), t.op("resume-1", "resume", resume="approve")
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1")
    t.ok("start-1", "worker-1")
    t.invoke(resume)
    t.dispatch("resume-1", "worker-1")
    t.w1_approve("worker-1", "resume-1", "T1", "approve")
    t.w1_execute("worker-1", "resume-1", "T1", "approve", stop=(point, "SIGKILL"))
    t.arms.append(
        ArmSpec(point=point, action="signal", signal="SIGKILL", worker="worker-1", op_id="resume-1")
    )
    t.faults.append(FaultSpec(kind="sigkill", arm=0))
    t.exit("worker-1", returncode=-9, signal="SIGKILL")
    t.dispatch("resume-1", "worker-2", redeliver=True)
    t.faults.append(FaultSpec(kind="redelivery", op_ids=("resume-1",), worker="worker-2"))
    t.get_tuple(t.workers["worker-2"], "resume-1", "T1")
    if rerun:
        _, attempt = t.w1_execute("worker-2", "resume-1", "T1", "approve")
    else:
        attempt = 1
    entry = ["charge:T1", attempt]
    t.w1_finish("worker-2", "resume-1", "T1", [entry], receipt=entry)
    t.ok("resume-1", "worker-2")
    t.exit("worker-2")
    return t.build()


def exception_case_trial(*, rerun: bool) -> TrialArtifacts:
    """LG-PW's own case: tasks A and B in one super-step; A's writes commit, B raises (its ERROR
    write is saved), the run fails; a second run on the thread runs B again and, with `rerun`,
    A too."""
    t = TrialBuilder(workload="w5")
    first, second = t.op("run-1", "turn"), t.op("run-2", "turn")
    t.invoke(first)
    t.dispatch("run-1", "worker-1")
    w = t.workers["worker-1"]
    t.get_tuple(w, "run-1", "T1")
    c0 = t.put(w, "run-1", "T1", source="input", updates={"__start__": {"go": 1}})
    t.node(w, "run-1", "a", "task-a")
    t.put_writes(w, "run-1", "T1", c0, "task-a", [("a", "done")])
    t.node(w, "run-1", "b", "task-b", end=False)
    t.put_writes(w, "run-1", "T1", c0, "task-b", [("__error__", "boom")])
    t.fail("run-1", "worker-1", "boom")
    t.invoke(second)
    t.dispatch("run-2", "worker-1")
    t.get_tuple(w, "run-2", "T1")
    if rerun:
        t.node(w, "run-2", "a", "task-a")
    t.node(w, "run-2", "b", "task-b")
    t.put_writes(w, "run-2", "T1", c0, "task-b", [("b", "done")])
    t.put(w, "run-2", "T1", source="loop", updates={"a": "done", "b": "done"})
    t.ok("run-2", "worker-1")
    t.exit("worker-1")
    return t.build()


def stop_and_redeliver_fork_trial() -> TrialArtifacts:
    """The C11-8796 shape on W5: worker-1 stops itself (SIGSTOP) at the turn's put.before, the
    turn is redelivered to worker-2, which completes; SIGCONT, and worker-1's put lands too."""
    t = TrialBuilder(workload="w5")
    turn = t.op("turn-1", "turn")
    t.invoke(turn)
    t.dispatch("turn-1", "worker-1")
    cout = t.w5_turn("worker-1", "turn-1", "T1", 1, stop_put="SIGSTOP")
    assert cout is None
    pending = next(iter(t.pending))
    t.arms.append(
        ArmSpec(
            point="put.before", action="signal", signal="SIGSTOP", worker="worker-1", op_id="turn-1"
        )
    )
    t.inject("stop-1", fault="stop", signal="SIGSTOP", proc="worker-1")
    t.dispatch("turn-1", "worker-2", redeliver=True)
    t.w5_turn("worker-2", "turn-1", "T1", 1)
    t.ok("turn-1", "worker-2")
    t.heal("stop-1", signal="SIGCONT")
    t.finish_put(t.workers["worker-1"], "turn-1", pending)
    t.exit("worker-1")
    t.exit("worker-2")
    t.faults.extend(
        [
            FaultSpec(kind="sigstop", arm=0, fault_id="stop-1"),
            FaultSpec(kind="redelivery", op_ids=("turn-1",), worker="worker-2"),
        ]
    )
    return t.build()


def lost_before_first_checkpoint_trial(
    *, evidence: str = "synthetic", **changes: Any
) -> TrialArtifacts:
    """The C11-8764 shape: SIGKILL at the start op's first put.before (the input checkpoint)."""
    t = TrialBuilder(evidence=evidence)
    start = t.op("start-1", "start")
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1", kill_input="SIGKILL")
    t.exit("worker-1", returncode=-9, signal="SIGKILL")
    t.info("start-1", "worker died")
    t.arms.append(
        ArmSpec(
            point="put.before",
            action="signal",
            signal="SIGKILL",
            worker="worker-1",
            op_id="start-1",
            landmark=changes.pop("landmark", None),
        )
    )
    t.faults.append(FaultSpec(kind="sigkill", arm=0))
    return t.build(**changes)
