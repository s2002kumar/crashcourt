"""I5 (single-writer lineage is a chain, every row readable) and I6 (correct head, no regression).

These read raw columns only (ids, parent ids, channel versions, blob keys), so a serializer defect
cannot fake or hide them.
"""

from __future__ import annotations

from crashcourt.checker.facts import Ack, checkpoint_pointer, data_int, data_text, pointer
from crashcourt.checker.invariants._common import Context, thread_witnesses, trial_witnesses
from crashcourt.checker.lineage import Lineage, channel_versions
from crashcourt.checker.observations import Anomaly, Observation, not_exercised, observed
from crashcourt.spec.events import Event

_I5_MISSING = "fewer than two checkpoint rows for every thread"


def _exercised_lineages(ctx: Context) -> list[Lineage]:
    """Lineages of the scenario's threads, if some thread has at least two checkpoint rows."""
    lineages = [lin for lin in ctx.lineages.values() if lin.thread_id in ctx.scenario.threads]
    threads = {lin.thread_id for lin in lineages}
    rows_per_thread = {
        t: sum(len(lin.rows) for lin in lineages if lin.thread_id == t) for t in threads
    }
    if not any(count >= 2 for count in rows_per_thread.values()):
        return []
    return lineages


def i5a(ctx: Context) -> Observation:
    """Each (thread, ns) lineage has exactly one root, and no row has two children (children an
    explicit update_state created excepted). Evaluated in every trial: in a multi-writer trial
    the fork is still observed, and Layer B classifies it."""
    lineages = _exercised_lineages(ctx)
    if not lineages:
        return not_exercised("I5a", _I5_MISSING)
    anomalies: list[Anomaly] = []
    for lin in lineages:
        where = (lin.thread_id, lin.checkpoint_ns)
        if len(lin.roots) != 1:
            anomalies.append(
                Anomaly(
                    evidence=tuple(checkpoint_pointer(*where, r) for r in lin.roots)
                    or (checkpoint_pointer(*where, str(lin.head)),),
                    sub_reason=f"lineage has {len(lin.roots)} roots",
                    witnesses=thread_witnesses(ctx, lin.thread_id),
                )
            )
        for parent, children in lin.forks:
            counted = [c for c in children if lin.rows[c].metadata.get("source") != "update"]
            if len(counted) < 2:
                continue
            anomalies.append(
                Anomaly(
                    evidence=(
                        checkpoint_pointer(*where, parent),
                        *(checkpoint_pointer(*where, c) for c in counted),
                    ),
                    sub_reason=f"fork: checkpoint {parent} has {len(counted)} children",
                    witnesses=thread_witnesses(ctx, lin.thread_id),
                )
            )
    return observed(
        "I5a", anomalies, witnesses=trial_witnesses(ctx, {lin.thread_id for lin in lineages})
    )


def i5b(ctx: Context) -> Observation:
    """No row names a parent id that has no row (a dangling parent), in any trial."""
    lineages = _exercised_lineages(ctx)
    if not lineages:
        return not_exercised("I5b", _I5_MISSING)
    anomalies = [
        Anomaly(
            evidence=(checkpoint_pointer(lin.thread_id, lin.checkpoint_ns, child),),
            sub_reason=f"dangling parent: checkpoint {child} names missing parent {parent}",
            witnesses=thread_witnesses(ctx, lin.thread_id),
        )
        for lin in lineages
        for child, parent in lin.dangling
    ]
    return observed(
        "I5b", anomalies, witnesses=trial_witnesses(ctx, {lin.thread_id for lin in lineages})
    )


def i5c(ctx: Context) -> Observation:
    """Every (channel, version) a checkpoint's channel_versions names has a checkpoint_blobs row,
    for the channels the workload's effect model declares blob-stored, unless the checkpoint holds
    the value inline."""
    lineages = _exercised_lineages(ctx)
    if not lineages:
        return not_exercised("I5c", _I5_MISSING)
    if not ctx.model.blob_channels:
        return not_exercised("I5c", "the workload's effect model declares no blob-stored channel")
    anomalies: list[Anomaly] = []
    for lin in lineages:
        for checkpoint_id, row in lin.rows.items():
            for channel, version in sorted(channel_versions(row).items()):
                if channel not in ctx.model.blob_channels or ctx.reader.inline(row, channel):
                    continue
                if ctx.reader.blob(row, channel) is None:
                    anomalies.append(
                        Anomaly(
                            evidence=(
                                checkpoint_pointer(lin.thread_id, lin.checkpoint_ns, checkpoint_id),
                            ),
                            sub_reason=(
                                f"missing blob: checkpoint {checkpoint_id} names {channel} "
                                f"version {version}"
                            ),
                            witnesses=thread_witnesses(ctx, lin.thread_id),
                        )
                    )
    return observed(
        "I5c", anomalies, witnesses=trial_witnesses(ctx, {lin.thread_id for lin in lineages})
    )


def _acks(ctx: Context, lin: Lineage) -> list[Ack]:
    return [
        a
        for a in ctx.facts.acks
        if a.thread_id == lin.thread_id and a.checkpoint_ns == lin.checkpoint_ns
    ]


def i6a(ctx: Context) -> Observation:
    """After quiescence, in a single-writer trial, the head is the last acknowledged checkpoint of
    the last writer (by that writer's seq). In a multi-writer trial the head is recorded with the
    writer that wrote it, as an anomaly Layer B classifies (the head is chosen by the larger id).

    Exercised: a lineage with a head and at least one acknowledged checkpoint.
    """
    facts = ctx.facts
    anomalies: list[Anomaly] = []
    threads: set[str] = set()
    for lin in ctx.lineages.values():
        if lin.thread_id not in ctx.scenario.threads or lin.head is None:
            continue
        acks = _acks(ctx, lin)
        if not acks:
            continue
        threads.add(lin.thread_id)
        head = checkpoint_pointer(lin.thread_id, lin.checkpoint_ns, lin.head)
        if facts.concurrency(lin.thread_id):
            writers = sorted({a.worker for a in acks if a.checkpoint_id == lin.head})
            anomalies.append(
                Anomaly(
                    evidence=(
                        head,
                        *(pointer(a.event) for a in acks if a.checkpoint_id == lin.head),
                    ),
                    sub_reason=(
                        f"multi-writer thread: head {lin.head} is the largest id, written by "
                        f"{', '.join(writers) or 'no acknowledging worker'}"
                    ),
                    witnesses=thread_witnesses(ctx, lin.thread_id),
                )
            )
            continue
        dispatched = [d for d in facts.dispatches_on(lin.thread_id)]
        acked_by = {a.worker for a in acks}
        last_writer = next(
            (
                data_text(d, "to")
                for d in sorted(dispatched, key=lambda d: d.seq, reverse=True)
                if data_text(d, "to") in acked_by
            ),
            None,
        )
        mine = [a for a in acks if a.worker == last_writer]
        if not mine:
            continue
        last = max(mine, key=lambda a: a.event.seq)
        if last.checkpoint_id != lin.head:
            anomalies.append(
                Anomaly(
                    evidence=(head, pointer(last.event)),
                    sub_reason=(
                        f"head {lin.head} is not {last_writer}'s last acknowledged checkpoint "
                        f"{last.checkpoint_id}"
                    ),
                    witnesses=thread_witnesses(ctx, lin.thread_id),
                )
            )
    if not threads:
        return not_exercised("I6a", "no lineage with a head and an acknowledged checkpoint")
    return observed("I6a", anomalies, witnesses=trial_witnesses(ctx, threads))


def i6b(ctx: Context) -> Observation:
    """No latest-checkpoint read returns an id smaller than a checkpoint the same process
    acknowledged earlier, or one another worker acknowledged before a client.ok that precedes this
    worker's dispatch.

    A latest read is a saver.get_tuple.return found with no checkpoint_id requested. Exercised: at
    least one such read with an earlier acknowledgment to compare against.
    """
    facts = ctx.facts
    anomalies: list[Anomaly] = []
    threads: set[str] = set()
    for worker in facts.workers:
        for read in facts.order.by_proc[worker]:
            if not _latest_read(read):
                continue
            thread_id, ns, got = (
                str(read.thread_id),
                read.checkpoint_ns or "",
                str(read.checkpoint_id),
            )
            earlier = [
                a
                for a in facts.acks
                if a.thread_id == thread_id
                and a.checkpoint_ns == ns
                and (
                    (a.worker == worker and a.event.seq < read.seq)
                    or (a.worker != worker and _acked_before_dispatch(ctx, a, read))
                )
            ]
            if not earlier:
                continue
            threads.add(thread_id)
            newer = [a for a in earlier if a.checkpoint_id > got]
            if newer:
                anomalies.append(
                    Anomaly(
                        evidence=(pointer(read), *(pointer(a.event) for a in newer)),
                        sub_reason=(
                            f"{worker} read {got} as latest after {newer[0].checkpoint_id} "
                            "was acknowledged"
                        ),
                        witnesses=thread_witnesses(ctx, thread_id),
                    )
                )
    if not threads:
        return not_exercised("I6b", "no latest read after an acknowledgment it can be compared to")
    return observed("I6b", anomalies, witnesses=trial_witnesses(ctx, threads))


def _latest_read(event: Event) -> bool:
    return (
        event.kind == "saver.get_tuple.return"
        and event.status == "ok"
        and event.data.get("found") is True
        and event.data.get("requested") is None
        and event.thread_id is not None
        and event.checkpoint_id is not None
    )


def _acked_before_dispatch(ctx: Context, ack: Ack, read: Event) -> bool:
    """Another worker's acknowledgment counts when a client.ok it answered (data.worker_seq at or
    after the acknowledgment) precedes the dispatch of the op this read belongs to."""
    facts = ctx.facts
    oks = [
        e.seq
        for e in facts.orch_events
        if e.kind == "client.ok"
        and data_text(e, "worker") == ack.worker
        and (data_int(e, "worker_seq") or -1) >= ack.event.seq
    ]
    record = facts.ops.get(str(read.op_id))
    if not oks or record is None:
        return False
    dispatches = [d.seq for d in record.dispatches if data_text(d, "to") == read.proc]
    return any(ok < d for ok in oks for d in dispatches)
