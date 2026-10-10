"""I1a (sync ordering), I1b (acknowledged checkpoints survive), I11 (client OK implies durable)."""

from __future__ import annotations

from collections import defaultdict

from crashcourt.checker.facts import checkpoint_pointer, data_int, data_text, pointer
from crashcourt.checker.invariants._common import Context, thread_witnesses, trial_witnesses
from crashcourt.checker.observations import (
    WRITER_CRASHED,
    Anomaly,
    Observation,
    Witness,
    not_exercised,
    observed,
)
from crashcourt.spec.events import Event


def i1a(ctx: Context) -> Observation:
    """Under sync, no node.start of step k+1 precedes, in the same process, the ok
    saver.put.return of the checkpoint for step k.

    A task belongs to the step after the checkpoint its writes are linked to (the checkpoint id on
    its saver.put_writes call), so the pair is (put of checkpoint C, node.start of a task linked to
    C), both in one process and one run (op). The first tick after the input checkpoint counts (the
    reading recorded for LG-DUR-SYNC). A node.start with no ok return of its checkpoint before it,
    in the run that called put for that checkpoint, is an anomaly; a later run that starts from the
    checkpoint is not paired with it. Exercised: at least one such pair.
    """
    if ctx.scenario.durability != "sync":
        return not_exercised("I1a", "durability is not sync; I1a is defined under sync only")
    facts = ctx.facts
    anomalies: list[Anomaly] = []
    threads: set[str] = set()
    pairs = 0
    for worker in facts.workers:
        events = facts.order.by_proc[worker]
        calls: dict[tuple[str | None, str, str, str], Event] = {}
        returned: dict[tuple[str | None, str, str, str], list[int]] = defaultdict(list)
        for event in events:
            if event.thread_id is None or event.checkpoint_id is None:
                continue
            key = (event.op_id, event.thread_id, event.checkpoint_ns or "", event.checkpoint_id)
            if event.kind == "saver.put.call":
                calls.setdefault(key, event)
            elif event.kind == "saver.put.return" and event.status == "ok":
                returned[key].append(event.seq)
        for start in events:
            if start.kind != "node.start" or start.task_id is None:
                continue
            for linked in facts.task_checkpoints.get(start.task_id, ()):
                key = (start.op_id, *linked)
                if key not in calls:
                    continue
                pairs += 1
                threads.add(linked[0])
                if not any(seq < start.seq for seq in returned[key]):
                    anomalies.append(
                        Anomaly(
                            evidence=(pointer(start), pointer(calls[key])),
                            sub_reason=(
                                f"task {start.task_id} started before the put of checkpoint "
                                f"{linked[2]} returned ok in {worker}"
                            ),
                            witnesses=thread_witnesses(ctx, linked[0]),
                        )
                    )
    if not pairs:
        return not_exercised(
            "I1a", "no (put of a checkpoint, node.start of a task after it) pair in one process"
        )
    return observed("I1a", anomalies, witnesses=trial_witnesses(ctx, threads))


def i1b(ctx: Context) -> Observation:
    """Every acknowledged checkpoint exists in the snapshot after quiescence.

    Exercised: at least one acknowledged checkpoint. Each missing one records whether its writer
    exited with a signal or a non-zero status (the condition LG-DUR-ASYNC's permission needs).
    """
    facts = ctx.facts
    present = {(r.thread_id, r.checkpoint_ns, r.checkpoint_id) for r in facts.snapshot.checkpoints}
    acks = [a for a in facts.acks if a.thread_id in ctx.scenario.threads]
    if not acks:
        return not_exercised("I1b", "no acknowledged checkpoint (saver.put.return ok)")
    anomalies: list[Anomaly] = []
    seen: set[tuple[str, str, str]] = set()
    for ack in acks:
        key = (ack.thread_id, ack.checkpoint_ns, ack.checkpoint_id)
        if key in present or key in seen:
            continue
        seen.add(key)
        exit_ = facts.exits.get(ack.worker)
        crashed = exit_ is not None and exit_.abnormal
        anomalies.append(
            Anomaly(
                evidence=(pointer(ack.event), checkpoint_pointer(*key)),
                sub_reason=f"acknowledged checkpoint {ack.checkpoint_id} is missing",
                witnesses=(
                    *thread_witnesses(ctx, ack.thread_id),
                    Witness(
                        name=WRITER_CRASHED,
                        held=crashed,
                        evidence=(pointer(exit_.event),) if exit_ is not None and crashed else (),
                    ),
                ),
            )
        )
    return observed("I1b", anomalies, witnesses=trial_witnesses(ctx, {a.thread_id for a in acks}))


def i11(ctx: Context) -> Observation:
    """A client OK implies the run's final checkpoint is durable.

    Final checkpoint: the last ok saver.put.return of the same op and thread in the answering
    worker, at or before the seq the client.ok names (data.worker_seq). Exercised: a client.ok with
    the result edge and such a put.
    """
    facts = ctx.facts
    present = {(r.thread_id, r.checkpoint_ns, r.checkpoint_id) for r in facts.snapshot.checkpoints}
    anomalies: list[Anomaly] = []
    threads: set[str] = set()
    for ok in facts.orch_events:
        worker, last = data_text(ok, "worker"), data_int(ok, "worker_seq")
        op = ctx.scenario.op(ok.op_id)
        if ok.kind != "client.ok" or worker is None or last is None or op is None:
            continue
        puts = [
            e
            for e in facts.order.by_proc.get(worker, ())[: last + 1]
            if e.kind == "saver.put.return"
            and e.status == "ok"
            and e.op_id == ok.op_id
            and e.thread_id == op.thread_id
            and e.checkpoint_id is not None
        ]
        if not puts:
            continue
        final = puts[-1]
        threads.add(op.thread_id)
        key = (op.thread_id, final.checkpoint_ns or "", str(final.checkpoint_id))
        if key not in present:
            anomalies.append(
                Anomaly(
                    evidence=(pointer(ok), pointer(final), checkpoint_pointer(*key)),
                    sub_reason=f"op {ok.op_id} returned ok; final checkpoint {key[2]} is missing",
                    witnesses=thread_witnesses(ctx, op.thread_id),
                )
            )
    if not threads:
        return not_exercised("I11", "no client.ok with a result edge and a put in its op")
    return observed("I11", anomalies, witnesses=trial_witnesses(ctx, threads))
