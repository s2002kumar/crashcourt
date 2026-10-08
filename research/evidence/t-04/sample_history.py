"""Write a scripted sample history and its merge (T-04 evidence). Not a result.

One Python process plays the orchestrator, two workers, and the effect sink through a forced
fork of an approval thread: both workers read head C4, pause at a barrier, are released
together, and each writes a child of C4; worker-1 also fires one effect. Then two operations go
wrong, so the history has every client outcome to label: op-9's effect fails and the client is
told so (client.fail), and worker-1 is killed with SIGKILL while writing op-10's checkpoint, so
the client cannot know whether it committed (client.info). All four histories are real
HistoryWriter output; they share one pid because one process wrote them.

    uv run python research/evidence/t-04/sample_history.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from crashcourt.history.merge import merge
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.spec.events import Event

HERE = Path(__file__).resolve().parent
OUT = HERE / "sample"
RUN = RunIdentity(batch="DEMO-t04-sample", cell="DEMO-pg-async-sync-W1-F06", trial=0, seed="0")
NAMES = ("orchestrator", "worker-1", "worker-2", "sink")
THREAD: dict[str, Any] = {"thread_id": "t3", "checkpoint_ns": ""}


def read_c4(writer: HistoryWriter, op: str) -> int:
    """get_tuple returns head C4, then the worker parks at a barrier; returns the hit's seq."""
    writer.emit("saver.get_tuple.call", op_id=op, **THREAD)
    writer.emit("saver.get_tuple.return", op_id=op, checkpoint_id="C4", status="ok", **THREAD)
    return writer.emit("fp.hit", op_id=op, fp="get_tuple.after", **THREAD).seq


def put_child(writer: HistoryWriter, op: str, child: str) -> Event:
    """Write a child of C4; returns the last event before the worker sends its result."""
    ids = {"op_id": op, "checkpoint_id": child, "parent_checkpoint_id": "C4", **THREAD}
    writer.emit("saver.put.call", fp="put.before", **ids)
    return writer.emit("saver.put.return", status="ok", **ids)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.iterdir():  # clear in place: a removed folder can linger on synced drives
        old.unlink()
    w = {name: HistoryWriter(OUT / f"{name}.jsonl", run=RUN, proc=name) for name in NAMES}
    orch, w1, w2, sink = (w[n] for n in NAMES)

    for op, worker, decision in (("op-7", "worker-1", "approve"), ("op-8", "worker-2", "reject")):
        logical = f"{decision}:t3:i1"
        orch.emit("client.invoke", op_id=op, logical_op=logical, data={"resume": decision})
        orch.emit("dispatch.send", op_id=op, logical_op=logical, data={"to": worker})
    hits = {"worker-1": read_c4(w1, "op-7"), "worker-2": read_c4(w2, "op-8")}
    for worker in ("worker-1", "worker-2"):
        orch.emit("fp.release", data={"proc": worker, "hit_seq": hits[worker]})

    w2.emit("fp.action", op_id="op-8", data={"hit_seq": hits["worker-2"], "action": "pause"})
    w2.emit("node.start", op_id="op-8", attempt=1, task_id="record", **THREAD)
    last_w2 = put_child(w2, "op-8", "C5b")

    w1.emit("fp.action", op_id="op-7", data={"hit_seq": hits["worker-1"], "action": "pause"})
    w1.emit("node.start", op_id="op-7", attempt=1, task_id="execute", **THREAD)
    effect: dict[str, Any] = {"op_id": "op-7", "effect_key": "charge:order-17", "attempt": 1}
    w1.emit("effect.request", checkpoint_id="C4", **effect, **THREAD)
    sink.emit("sink.append", **effect, data={"worker": "worker-1", "sink_seq": 1})
    w1.emit("effect.response", status="ok", **effect, data={"sink_seq": 1}, **THREAD)
    last_w1 = put_child(w1, "op-7", "C5a")

    # Each result names the worker's last event before it answered (merge rule 5).
    for op, last in (("op-8", last_w2), ("op-7", last_w1)):
        result = {"worker": last.proc, "worker_seq": last.seq}
        orch.emit("client.ok", op_id=op, status="ok", data=result)

    # op-9 fails: the effect call to the sink errors out, and the worker reports the failure.
    orch.emit("client.invoke", op_id="op-9", logical_op="notify:t3:i2")
    orch.emit("dispatch.send", op_id="op-9", logical_op="notify:t3:i2", data={"to": "worker-2"})
    w2.emit("node.start", op_id="op-9", attempt=1, task_id="notify", **THREAD)
    failed: dict[str, Any] = {"op_id": "op-9", "effect_key": "notify:t3", "attempt": 1}
    w2.emit("effect.request", checkpoint_id="C5b", **failed, **THREAD)
    last_fail = w2.emit(
        "effect.response", status="fail", error="ConnectionResetError", **failed, **THREAD
    )
    result = {"worker": last_fail.proc, "worker_seq": last_fail.seq}
    orch.emit("client.fail", op_id="op-9", status="fail", error="effect failed", data=result)

    # op-10: worker-1 is killed mid-put, so the outcome is unknown to the client.
    orch.emit("client.invoke", op_id="op-10", logical_op="approve:t3:i3")
    orch.emit("dispatch.send", op_id="op-10", logical_op="approve:t3:i3", data={"to": "worker-1"})
    w1.emit("node.start", op_id="op-10", attempt=1, task_id="execute", **THREAD)
    ids = {"op_id": "op-10", "checkpoint_id": "C6a", "parent_checkpoint_id": "C5a", **THREAD}
    w1.emit("saver.put.call", fp="put.before", **ids)
    fault = orch.emit(
        "fault.inject", fault_id="F-1", data={"signal": "SIGKILL", "proc": "worker-1"}
    )
    orch.emit("worker.exit", data={"proc": "worker-1", "returncode": -9, "signal": "SIGKILL"})
    orch.emit("client.info", op_id="op-10", status="info", error="worker killed mid-put")
    orch.emit("fault.heal", fault_id=fault.fault_id)
    orch.emit("worker.exit", data={"proc": "worker-2", "returncode": 0})
    for writer in w.values():
        writer.close()

    merged = merge(read_history(OUT / f"{name}.jsonl").events for name in NAMES)
    (OUT / "merged.jsonl").write_bytes("".join(e.to_line() for e in merged).encode())
    rows = [f"{'#':>3}  {'proc':<12} {'seq':>3}  {'kind':<24} detail"]
    for index, e in enumerate(merged):
        fields = (
            ("op", e.op_id),
            ("cp", e.checkpoint_id),
            ("parent", e.parent_checkpoint_id),
            ("effect", e.effect_key),
            ("status", e.status),
            ("error", e.error),
            ("data", e.data or None),
        )
        detail = " ".join(f"{k}={v}" for k, v in fields if v is not None)
        rows.append(f"{index:>3}  {e.proc:<12} {e.seq:>3}  {e.kind:<24} {detail}")
    (OUT / "merged.txt").write_bytes(("\n".join(rows) + "\n").encode())


if __name__ == "__main__":
    main()
