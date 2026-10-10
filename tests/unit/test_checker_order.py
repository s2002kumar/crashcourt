"""The trusted order: happens-before from seq and T-04's causal edges, never from a clock."""

from __future__ import annotations

import pytest

from crashcourt.checker.order import Order, ref
from crashcourt.history.merge import Ref, group_by_process
from crashcourt.history.reader import HistoryError
from crashcourt.spec.events import Event
from tests.unit import _trials as T


def by_proc(builder: T.TrialBuilder) -> dict[str, tuple[Event, ...]]:
    procs = [builder.orch, *builder.workers.values()]
    if builder.sink.events:
        procs.append(builder.sink)
    return group_by_process(p.events for p in procs)


def test_seq_dispatch_result_and_exit_edges() -> None:
    t = T.TrialBuilder()
    spec = t.op("start-1", "start")
    invoke = t.invoke(spec)
    dispatch = t.dispatch("start-1", "worker-1")
    w = t.workers["worker-1"]
    read = t.get_tuple(w, "start-1", "T1")
    ok = t.ok("start-1", "worker-1")
    other = t.workers.setdefault(
        "worker-2", T.Proc("worker-2", 999, t.batch, t.cell, t.trial, t.clock)
    )
    lone = other.emit("saver.get_tuple.call", thread_id="T1")
    exit_ = t.exit("worker-1")
    order = Order(by_proc(t))
    assert order.before(ref(invoke), ref(dispatch))
    assert order.before(ref(dispatch), ref(read))  # dispatch edge
    assert order.before(ref(read), ref(ok))  # result edge
    assert order.before(ref(read), ref(exit_))  # exit edge
    assert not order.before(ref(ok), ref(read))
    assert not order.before(ref(read), ref(read))
    # A process no edge reaches is unordered with everything else.
    assert not order.before(ref(lone), ref(ok)) and not order.before(ref(ok), ref(lone))


def test_a_contradiction_is_a_history_error() -> None:
    t = T.TrialBuilder()
    t.invoke(t.op("start-1", "start"))
    t.dispatch("start-1", "worker-1")
    w = t.workers["worker-1"]
    w.emit(
        "effect.response",
        op_id="start-1",
        attempt=1,
        effect_key="k",
        status="ok",
        data={"sink_seq": 7},
    )
    with pytest.raises(HistoryError):
        Order(by_proc(t))


def test_the_order_does_not_depend_on_clocks() -> None:
    t = T.TrialBuilder()
    t.invoke(t.op("start-1", "start"))
    t.dispatch("start-1", "worker-1")
    t.get_tuple(t.workers["worker-1"], "start-1", "T1")
    t.ok("start-1", "worker-1")
    procs = by_proc(t)
    reversed_clocks = {
        p: tuple(e.model_copy(update={"mono_ns": 10**12 - e.mono_ns}) for e in events)
        for p, events in procs.items()
    }
    a, b = Order(procs), Order(reversed_clocks)
    refs = [Ref(p, e.seq) for p, events in procs.items() for e in events]
    assert all(a.before(x, y) == b.before(x, y) for x in refs for y in refs)
