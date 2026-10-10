"""Merge per-process histories into one causal order.

Within a process, `seq` is a total order. Across processes the merge uses only these edges,
each of which holds in every real execution:

1. Dispatch: the earliest `dispatch.send` or `dispatch.redeliver` of op X to worker W precedes
   W's first event with op_id X. A worker cannot act on an op it has not been sent.
2. Barrier: the `fp.hit` at W's seq n precedes the `fp.release` naming (W, n), which precedes
   W's `fp.action` for hit n.
3. Effect: for one (worker, op_id, effect_key, attempt), the worker's first `effect.request`
   precedes the sink's first `sink.append`. Each `sink.append` precedes the worker's ok
   `effect.response` that names its ledger row (data.sink_seq). A failed response names no row
   and gets no edge, so a failure followed by a successful retry cannot be reordered. Both
   edges rest on write order: the worker logs a request before sending it, and the sink logs an
   append before answering (crashcourt.spec.events). A ledger row that appears twice, or an ok
   response whose row is missing or belongs to another request, contradicts the trial.
4. Exit: a worker's last event precedes the `worker.exit` naming it (recorded from waitpid).
5. Result: the worker event named by a `client.ok` or `client.fail` (data.worker, data.worker_seq,
   the worker's last event before it sent its result) precedes that client event.

Two tempting edges are deliberately absent. `worker.start` is not ordered before the worker's
first event, because a child can log before the parent records the spawn. A worker's later
events are not ordered before `client.ok`, because background saver writes can finish after the
result is sent; only the event the result names is. `mono_ns` only breaks ties between events no
edge orders; wall clocks are never used. A cycle means the history contradicts itself, so the
trial is INVALID.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from crashcourt.history.reader import HistoryError
from crashcourt.spec.events import Event


@dataclass(frozen=True, order=True)
class Ref:
    """One event's position: process name and per-process sequence number."""

    proc: str
    seq: int


Edge = tuple[Ref, Ref]


def _ref(event: Event) -> Ref:
    return Ref(event.proc, event.seq)


def group_by_process(histories: Iterable[Sequence[Event]]) -> dict[str, tuple[Event, ...]]:
    """Check each history is one process's complete, contiguous record, and key them by proc."""
    by_proc: dict[str, tuple[Event, ...]] = {}
    identity: tuple[str, str, int, str] | None = None
    for history in histories:
        if not history:
            continue
        procs = {e.proc for e in history}
        if len(procs) != 1:
            raise HistoryError(f"one history holds several processes: {sorted(procs)}")
        proc = history[0].proc
        if proc in by_proc:
            raise HistoryError(f"two histories for process {proc}")
        if [e.seq for e in history] != list(range(len(history))):
            raise HistoryError(f"{proc}: seq is not contiguous from 0")
        for event in history:
            run = (event.batch, event.cell, event.trial, event.seed)
            if identity is None:
                identity = run
            elif run != identity:
                raise HistoryError(f"{proc} seq {event.seq}: belongs to another trial {run}")
        by_proc[proc] = tuple(history)
    return by_proc


def causal_edges(by_proc: Mapping[str, Sequence[Event]]) -> set[Edge]:
    """The cross-process edges listed in the module docstring."""
    edges: set[Edge] = set()
    first_with_op: dict[tuple[str, str], Event] = {}
    first_request: dict[tuple[str, str, str, int], Event] = {}
    acknowledged: list[Event] = []
    actions: dict[tuple[str, int], list[Event]] = defaultdict(list)
    for proc, events in by_proc.items():
        for event in events:
            if event.op_id is not None:
                first_with_op.setdefault((proc, event.op_id), event)
            if event.kind == "effect.request":
                key = (proc, str(event.op_id), str(event.effect_key), int(event.attempt or 0))
                first_request.setdefault(key, event)
            if event.kind == "effect.response" and event.status == "ok":
                acknowledged.append(event)
            if event.kind == "fp.action":
                actions[(proc, _count(event, "hit_seq"))].append(event)

    earliest_dispatch: dict[tuple[str, str, str], Event] = {}
    first_append: dict[tuple[str, str, str, int], Event] = {}
    append_by_row: dict[int, Event] = {}
    for proc, events in by_proc.items():
        for event in events:
            if event.kind in {"dispatch.send", "dispatch.redeliver"}:
                earliest_dispatch.setdefault((proc, _text(event, "to"), str(event.op_id)), event)
            elif event.kind == "fp.release":
                edges.update(_barrier_edges(by_proc, event, actions))
            elif event.kind == "sink.append":
                key = (
                    _text(event, "worker"),
                    str(event.op_id),
                    str(event.effect_key),
                    int(event.attempt or 0),
                )
                first_append.setdefault(key, event)
                row = _count(event, "sink_seq")
                if row in append_by_row:
                    raise HistoryError(f"{proc} seq {event.seq}: ledger row {row} appended twice")
                append_by_row[row] = event
            elif event.kind == "worker.exit":
                worker = by_proc.get(_text(event, "proc"))
                if worker:
                    edges.add((_ref(worker[-1]), _ref(event)))
            elif event.kind in {"client.ok", "client.fail"} and "worker" in event.data:
                edges.add((_named(by_proc, event, "worker", "worker_seq"), _ref(event)))

    for (_, worker, op_id), dispatch in earliest_dispatch.items():
        if (first := first_with_op.get((worker, op_id))) is not None:
            edges.add((_ref(dispatch), _ref(first)))
    for key, append in first_append.items():
        if (request := first_request.get(key)) is not None:
            edges.add((_ref(request), _ref(append)))
    for response in acknowledged:
        # The sink writes its append before it answers, so an ok response whose row is missing
        # from the sink's history (or with no sink history at all) contradicts the trial.
        row = _count(response, "sink_seq")
        where = f"{response.proc} seq {response.seq}: effect.response names ledger row {row}"
        append = append_by_row.get(row)
        if append is None:
            raise HistoryError(f"{where}, which the sink's history does not hold")
        asked = (response.proc, response.op_id, response.effect_key, response.attempt)
        stored = (_text(append, "worker"), append.op_id, append.effect_key, append.attempt)
        if asked != stored:
            raise HistoryError(f"{where}, which the sink appended for another request {stored}")
        edges.add((_ref(append), _ref(response)))
    return {(a, b) for a, b in edges if a.proc != b.proc}


def _barrier_edges(
    by_proc: Mapping[str, Sequence[Event]],
    release: Event,
    actions: Mapping[tuple[str, int], Sequence[Event]],
) -> list[Edge]:
    worker, hit_seq = _text(release, "proc"), _count(release, "hit_seq")
    events = by_proc.get(worker, ())
    if hit_seq >= len(events) or events[hit_seq].kind != "fp.hit":
        raise HistoryError(
            f"{release.proc} seq {release.seq}: fp.release names {worker} seq {hit_seq}, "
            "which is not an fp.hit"
        )
    edges: list[Edge] = [(_ref(events[hit_seq]), _ref(release))]
    edges.extend((_ref(release), _ref(action)) for action in actions.get((worker, hit_seq), ()))
    return edges


def _named(
    by_proc: Mapping[str, Sequence[Event]], event: Event, proc_key: str, seq_key: str
) -> Ref:
    """The event another process names by (proc, seq) in `data`; it must exist."""
    proc, seq = _text(event, proc_key), _count(event, seq_key)
    if not 0 <= seq < len(by_proc.get(proc, ())):
        raise HistoryError(
            f"{event.proc} seq {event.seq}: {event.kind} names {proc} seq {seq}, "
            "which is not in that history"
        )
    return Ref(proc, seq)


def _text(event: Event, key: str) -> str:
    value = event.data.get(key)
    return value if isinstance(value, str) else ""


def _count(event: Event, key: str) -> int:
    value = event.data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def merge(histories: Iterable[Sequence[Event]]) -> tuple[Event, ...]:
    """All events in one order that respects `seq` and every causal edge.

    Among events no edge orders, the smallest (mono_ns, proc, seq) comes first, so the result
    is fully determined by the events and independent of the order the histories are given in.
    """
    by_proc = group_by_process(histories)
    successors: dict[Ref, list[Ref]] = defaultdict(list)
    indegree: dict[Ref, int] = {}
    events: dict[Ref, Event] = {}
    for proc_events in by_proc.values():
        for index, event in enumerate(proc_events):
            ref = _ref(event)
            events[ref] = event
            indegree[ref] = 0 if index == 0 else 1
            if index:
                successors[_ref(proc_events[index - 1])].append(ref)
    for before, after in causal_edges(by_proc):
        successors[before].append(after)
        indegree[after] += 1

    def key(ref: Ref) -> tuple[int, str, int]:
        return (events[ref].mono_ns, ref.proc, ref.seq)

    ready = [(key(ref), ref) for ref, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[Event] = []
    while ready:
        _, ref = heapq.heappop(ready)
        ordered.append(events[ref])
        for after in successors[ref]:
            indegree[after] -= 1
            if indegree[after] == 0:
                heapq.heappush(ready, (key(after), after))
    if len(ordered) != len(events):
        stuck = sorted(ref for ref, degree in indegree.items() if degree > 0)[:5]
        raise HistoryError(f"causal edges form a cycle; first events involved: {stuck}")
    return tuple(ordered)
