"""The barrier book: hits map to barriers, and a step fires once, when all its barriers are held."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from crashcourt.orchestrator.barriers import BarrierBook, Held, ScheduleError
from crashcourt.spec.protocol import ArmMessage, Hit
from crashcourt.spec.scenario import parse_scenario

DATA: dict[str, Any] = {
    "v": 1,
    "name": "b",
    "cell": "c",
    "seed": "s",
    "workload": "smoke",
    "target": {"saver": "postgres", "driver": "async", "durability": "sync"},
    "workers": ["w1", "w2"],
    "ops": [
        {"id": "op-1", "worker": "w1", "thread": "t1", "after": "start"},
        {"id": "op-2", "worker": "w2", "thread": "t2", "after": "start"},
    ],
    "schedule": [
        {"on": {"fp": "get_tuple.after", "worker": "w1", "op": "op-1"}, "do": {"pause": "B1"}},
        {
            "on": {"fp": "put.before", "worker": "w2", "op": "op-2", "occurrence": 2},
            "do": {"pause": "B2"},
        },
        {"on": {"fp": "put.after", "worker": "w2", "op": "op-2"}, "do": {"signal": "SIGKILL"}},
        {"on": {"all_paused": ["B1", "B2"]}, "do": {"release": ["B1", "B2"]}},
    ],
}


def book() -> BarrierBook:
    return BarrierBook(parse_scenario(copy.deepcopy(DATA)))


def hit(fp: str, op: str | None, seq: int, occurrence: int = 1) -> Hit:
    return Hit(hit_seq=seq, fp=fp, op_id=op, occurrence=occurrence)  # type: ignore[arg-type]


def test_each_worker_gets_its_own_arms_in_schedule_order() -> None:
    b = book()
    assert b.arms("w1") == [ArmMessage(point="get_tuple.after", action="pause", op_id="op-1")]
    assert b.arms("w2") == [
        ArmMessage(point="put.before", action="pause", op_id="op-2", occurrence=2),
        ArmMessage(point="put.after", action="signal", op_id="op-2", signal="SIGKILL"),
    ]


def test_a_hit_holds_the_barrier_its_step_names() -> None:
    b = book()
    assert b.hit("w1", hit("get_tuple.after", "op-1", 3)) == Held("B1", "w1", 3, "op-1")
    assert b.held == [Held("B1", "w1", 3, "op-1")]


def test_a_step_fires_once_when_all_its_barriers_are_held() -> None:
    b = book()
    b.hit("w1", hit("get_tuple.after", "op-1", 3))
    assert b.due() == [] and b.unfired == [3]  # only B1 is held
    b.hit("w2", hit("put.before", "op-2", 9, occurrence=2))
    assert [i for i, _ in b.due()] == [3]
    assert b.due() == []  # it fires once
    assert b.release(("B1", "B2")) == [
        Held("B1", "w1", 3, "op-1"),
        Held("B2", "w2", 9, "op-2"),
    ]
    assert b.held == [] and b.fired == [3] and b.unfired == []


def test_the_book_knows_which_ops_are_parked() -> None:
    b = book()
    b.hit("w1", hit("get_tuple.after", "op-1", 3))
    assert b.parked("op-1") and not b.parked("op-2")
    b.release(("B1",))
    assert not b.parked("op-1")


@pytest.mark.parametrize(
    ("worker", "message"),
    [
        ("w1", hit("put.before", "op-1", 1)),  # no step pauses there
        ("w2", hit("put.before", "op-2", 1, occurrence=1)),  # the step pauses occurrence 2
        ("w2", hit("get_tuple.after", "op-1", 1)),  # B1's step is w1's
    ],
)
def test_a_hit_no_step_pauses_is_refused(worker: str, message: Hit) -> None:
    with pytest.raises(ScheduleError, match="no step pauses"):
        book().hit(worker, message)


def test_a_barrier_is_held_at_most_once() -> None:
    b = book()
    b.hit("w1", hit("get_tuple.after", "op-1", 3))
    b.release(("B1",))
    with pytest.raises(ScheduleError, match="twice"):
        b.hit("w1", hit("get_tuple.after", "op-1", 5))


def test_a_dead_worker_holds_nothing() -> None:
    b = book()
    b.hit("w1", hit("get_tuple.after", "op-1", 3))
    assert b.drop_worker("w1") == [Held("B1", "w1", 3, "op-1")]
    assert b.held == []
