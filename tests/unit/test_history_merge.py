"""Merge tests: hand-built cases, plus properties over histories from a simulated system.

The simulation plays an orchestrator, two workers, and an effect sink through dispatches,
redeliveries, effects, barriers, and exits, obeying real causality (a worker only acts on ops it
was sent, a release follows its hit, and so on). Each event remembers its true global position,
so the properties can check the merge against what actually happened.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crashcourt.history.merge import Ref, causal_edges, group_by_process, merge
from crashcourt.history.reader import HistoryError
from crashcourt.spec.events import Event

WORKERS = ("worker-1", "worker-2")
ORCH, SINK = "orchestrator", "sink"


def ev(proc: str, seq: int, kind: str, mono: int = 0, **fields: Any) -> Event:
    return Event.model_validate(
        {
            "v": 1,
            "batch": "DEMO-b",
            "cell": "DEMO-c",
            "trial": 0,
            "seed": "s",
            "proc": proc,
            "pid": 1,
            "seq": seq,
            "mono_ns": mono,
            "wall_ns": 0,
            "kind": kind,
            **fields,
        }
    )


class Simulation:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.next_seq: dict[str, int] = dict.fromkeys((ORCH, SINK, *WORKERS), 0)
        self.sent: dict[str, list[str]] = {w: [] for w in WORKERS}
        self.requests: list[tuple[str, str, str, int]] = []
        self.appended: list[tuple[str, str, str, int]] = []
        self.hits: list[tuple[str, int]] = []
        self.released: list[tuple[str, int]] = []
        self.exited: set[str] = set()
        self.ops = 0

    def emit(self, proc: str, kind: str, **fields: Any) -> int:
        seq = self.next_seq[proc]
        self.next_seq[proc] += 1
        self.records.append({"proc": proc, "seq": seq, "kind": kind, **fields})
        return seq

    def options(self) -> dict[str, Callable[[st.DrawFn], None]]:
        opts: dict[str, Callable[[st.DrawFn], None]] = {"noise": self.noise}
        live = [w for w in WORKERS if w not in self.exited]
        if live:
            opts["dispatch"] = lambda draw: self.dispatch(draw(st.sampled_from(live)))
            opts["hit"] = lambda draw: self.hit(draw(st.sampled_from(live)))
            opts["exit"] = lambda draw: self.exit(draw(st.sampled_from(live)))
        busy = [w for w in live if self.sent[w]]
        if busy:
            opts["work"] = lambda draw: self.work(draw, draw(st.sampled_from(busy)))
            opts["request"] = lambda draw: self.request(draw, draw(st.sampled_from(busy)))
        if live and any(self.sent.values()):
            opts["redeliver"] = lambda draw: self.redeliver(draw, draw(st.sampled_from(live)))
        if self.requests:
            opts["append"] = lambda draw: self.append(draw(st.sampled_from(self.requests)))
        answerable = [a for a in self.appended if a[0] in live]
        if answerable:
            opts["respond"] = lambda draw: self.respond(draw(st.sampled_from(answerable)))
        unreleased = [h for h in self.hits if h not in self.released]
        if unreleased:
            opts["release"] = lambda draw: self.release(draw(st.sampled_from(unreleased)))
        acting = [h for h in self.released if h[0] in live]
        if acting:
            opts["action"] = lambda draw: self.action(draw(st.sampled_from(acting)))
        answered = [w for w in WORKERS if self.sent[w] and self.next_seq[w]]
        if answered:
            opts["result"] = lambda draw: self.result(draw, draw(st.sampled_from(answered)))
        return opts

    def result(self, draw: st.DrawFn, worker: str) -> None:
        op = draw(st.sampled_from(self.sent[worker]))
        last = self.next_seq[worker] - 1
        kind = draw(st.sampled_from(["client.ok", "client.fail"]))
        self.emit(ORCH, kind, op_id=op, data={"worker": worker, "worker_seq": last})

    def dispatch(self, worker: str) -> None:
        self.ops += 1
        op = f"op-{self.ops}"
        self.emit(ORCH, "client.invoke", op_id=op)
        self.emit(ORCH, "dispatch.send", op_id=op, data={"to": worker})
        self.sent[worker].append(op)

    def redeliver(self, draw: st.DrawFn, worker: str) -> None:
        op = draw(st.sampled_from(sorted({o for ops in self.sent.values() for o in ops})))
        self.emit(ORCH, "dispatch.redeliver", op_id=op, data={"to": worker})
        self.sent[worker].append(op)

    def work(self, draw: st.DrawFn, worker: str) -> None:
        op = draw(st.sampled_from(self.sent[worker]))
        self.emit(worker, draw(st.sampled_from(["node.start", "saver.put.call"])), op_id=op)

    def request(self, draw: st.DrawFn, worker: str) -> None:
        op = draw(st.sampled_from(self.sent[worker]))
        key, attempt = f"k-{draw(st.integers(1, 2))}", draw(st.integers(1, 2))
        self.emit(worker, "effect.request", op_id=op, effect_key=key, attempt=attempt)
        self.requests.append((worker, op, key, attempt))

    def append(self, triple: tuple[str, str, str, int]) -> None:
        worker, op, key, attempt = triple
        self.emit(
            SINK, "sink.append", op_id=op, effect_key=key, attempt=attempt, data={"worker": worker}
        )
        self.appended.append(triple)

    def respond(self, triple: tuple[str, str, str, int]) -> None:
        worker, op, key, attempt = triple
        self.emit(worker, "effect.response", op_id=op, effect_key=key, attempt=attempt)

    def hit(self, worker: str) -> None:
        self.hits.append((worker, self.emit(worker, "fp.hit", fp="put.before")))

    def release(self, hit: tuple[str, int]) -> None:
        self.emit(ORCH, "fp.release", data={"proc": hit[0], "hit_seq": hit[1]})
        self.released.append(hit)

    def action(self, hit: tuple[str, int]) -> None:
        self.emit(hit[0], "fp.action", data={"hit_seq": hit[1], "action": "pause"})

    def exit(self, worker: str) -> None:
        self.emit(ORCH, "worker.exit", data={"proc": worker})
        self.exited.add(worker)

    def noise(self, draw: st.DrawFn) -> None:
        self.emit(ORCH, "fault.inject", fault_id=f"f-{len(self.records)}")


@st.composite
def simulated(draw: st.DrawFn) -> tuple[list[Event], bool]:
    """Events in their true global order, and whether mono_ns agrees with that order."""
    sim = Simulation()
    for _ in range(draw(st.integers(0, 45))):
        options = sim.options()
        options[draw(st.sampled_from(sorted(options)))](draw)
    causal_clocks = draw(st.booleans())
    events = [
        ev(mono=index * 10 if causal_clocks else draw(st.integers(0, 30)), **record)
        for index, record in enumerate(sim.records)
    ]
    return events, causal_clocks


def split(events: Sequence[Event]) -> list[list[Event]]:
    by_proc: dict[str, list[Event]] = {}
    for event in events:
        by_proc.setdefault(event.proc, []).append(event)
    return list(by_proc.values())


@settings(max_examples=300)
@given(simulated())
def test_merge_respects_seq_and_every_edge(case: tuple[list[Event], bool]) -> None:
    truth, _ = case
    merged = merge(split(truth))
    position = {Ref(e.proc, e.seq): i for i, e in enumerate(merged)}
    assert sorted(position) == sorted(Ref(e.proc, e.seq) for e in truth)
    for proc_events in split(merged):
        assert [e.seq for e in proc_events] == list(range(len(proc_events)))
    for before, after in causal_edges(group_by_process(split(truth))):
        assert position[before] < position[after]


@settings(max_examples=300)
@given(simulated())
def test_every_derived_edge_holds_in_the_true_order(case: tuple[list[Event], bool]) -> None:
    truth, _ = case
    actual = {Ref(e.proc, e.seq): i for i, e in enumerate(truth)}
    for before, after in causal_edges(group_by_process(split(truth))):
        assert actual[before] < actual[after]


@settings(max_examples=200)
@given(simulated(), st.randoms())
def test_merge_does_not_depend_on_input_order(case: tuple[list[Event], bool], rng: Any) -> None:
    truth, _ = case
    histories = split(truth)
    shuffled = histories[:]
    rng.shuffle(shuffled)
    assert merge(shuffled) == merge(histories)


@settings(max_examples=200)
@given(simulated())
def test_causal_clocks_reproduce_the_true_order(case: tuple[list[Event], bool]) -> None:
    truth, causal_clocks = case
    if causal_clocks:
        assert list(merge(split(truth))) == truth


def test_edges_override_clocks() -> None:
    worker = [
        ev("worker-1", 0, "node.start", 50, op_id="op-1"),
        ev("worker-1", 1, "effect.request", 90, op_id="op-1", effect_key="k", attempt=1),
    ]
    orchestrator = [ev(ORCH, 0, "dispatch.send", 60, op_id="op-1", data={"to": "worker-1"})]
    append = {"op_id": "op-1", "effect_key": "k", "attempt": 1, "data": {"worker": "worker-1"}}
    sink = [ev(SINK, 0, "sink.append", 5, **append)]
    order = [(e.proc, e.seq) for e in merge([sink, worker, orchestrator])]
    assert order == [(ORCH, 0), ("worker-1", 0), ("worker-1", 1), (SINK, 0)]


def test_result_follows_the_worker_event_it_names() -> None:
    worker = [
        ev("worker-1", 0, "node.start", 40, op_id="op-1"),
        ev("worker-1", 1, "saver.put.return", 90, op_id="op-1"),
        ev("worker-1", 2, "saver.put_writes.return", 95, op_id="op-1"),
    ]
    orchestrator = [
        ev(ORCH, 0, "dispatch.send", 10, op_id="op-1", data={"to": "worker-1"}),
        ev(ORCH, 1, "client.ok", 50, op_id="op-1", data={"worker": "worker-1", "worker_seq": 1}),
    ]
    order = [(e.proc, e.seq) for e in merge([orchestrator, worker])]
    # client.ok follows worker seq 1 despite its earlier clock; worker seq 2 (a background
    # write finishing after the result) stays unordered against it and sorts by clock.
    assert order == [(ORCH, 0), ("worker-1", 0), ("worker-1", 1), (ORCH, 1), ("worker-1", 2)]


def test_result_naming_a_missing_worker_event_is_rejected() -> None:
    result = {"worker": "worker-1", "worker_seq": 5}
    orchestrator = [ev(ORCH, 0, "client.ok", op_id="op-1", data=result)]
    worker = [ev("worker-1", 0, "node.start", op_id="op-1")]
    with pytest.raises(HistoryError, match="worker-1 seq 5"):
        merge([orchestrator, worker])


def test_contradictory_edges_are_a_cycle() -> None:
    orchestrator = [
        ev(ORCH, 0, "fp.release", data={"proc": "worker-1", "hit_seq": 0}),
        ev(ORCH, 1, "dispatch.send", op_id="op-1", data={"to": "worker-1"}),
    ]
    worker = [ev("worker-1", 0, "fp.hit", fp="put.before", op_id="op-1")]
    with pytest.raises(HistoryError, match="cycle"):
        merge([orchestrator, worker])


def test_release_must_name_a_hit() -> None:
    orchestrator = [ev(ORCH, 0, "fp.release", data={"proc": "worker-1", "hit_seq": 0})]
    worker = [ev("worker-1", 0, "node.start")]
    with pytest.raises(HistoryError, match=r"not an fp\.hit"):
        merge([orchestrator, worker])


@pytest.mark.parametrize(
    ("histories", "message"),
    [
        ([[ev("w", 0, "node.start"), ev("w", 2, "node.end")]], "not contiguous"),
        ([[ev("w", 1, "node.start")]], "not contiguous"),
        ([[ev("w", 0, "node.start"), ev("x", 1, "node.end")]], "several processes"),
        ([[ev("w", 0, "node.start")], [ev("w", 0, "node.start")]], "two histories"),
    ],
)
def test_malformed_histories_are_rejected(histories: list[list[Event]], message: str) -> None:
    with pytest.raises(HistoryError, match=message):
        merge(histories)


def test_histories_from_different_trials_are_rejected() -> None:
    other = ev("x", 0, "node.start").model_copy(update={"trial": 1})
    with pytest.raises(HistoryError, match="another trial"):
        merge([[ev("w", 0, "node.start")], [other]])
