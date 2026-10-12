"""Redelivery in the trial engine, with in-process fake workers.

K1a's shape: a worker ends at a failpoint and its replacement takes the op over. K3's shape: a
worker is held at a barrier, another takes the op over, and the held, now stale, run is released
once the op has ended. The client sees one invoke and one terminal event either way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from crashcourt.spec.protocol import Hit, ReleaseMessage, Result, Run, Shutdown

from .test_dispatcher import BASE, FakeWorker, Trial, obliging, terminal_counts

KILL_AT_WRITES: dict[str, Any] = {
    "on": {"fp": "put_writes.before", "worker": "w1", "op": "resume-1", "occurrence": 3},
    "do": {"signal": "SIGKILL"},
}
REPLACE: dict[str, Any] = {
    "on": {"exited": "w1"},
    "do": {"redeliver": {"op": "resume-1", "to": "w2"}},
}
HOLD: dict[str, Any] = {
    "on": {"fp": "put.before", "worker": "w1", "op": "turn-1", "occurrence": 3},
    "do": {"pause": "A"},
}
TAKE_OVER: dict[str, Any] = {
    "on": {"all_paused": ["A"]},
    "do": {"redeliver": {"op": "turn-1", "to": "w2"}},
}
RELEASE_AFTER: dict[str, Any] = {"on": {"ended": "turn-1"}, "do": {"release": ["A"]}}


def k1a(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        **BASE,
        "ops": [
            {"id": "start-1", "worker": "w1", "thread": "t1", "after": "start"},
            {"id": "resume-1", "worker": "w1", "thread": "t1", "after": "start-1", "resume": "ok"},
        ],
        "schedule": [KILL_AT_WRITES, REPLACE],
    }
    data.update(changes)
    return data


def k3(schedule: list[dict[str, Any]] | None = None, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        **BASE,
        "ops": [{"id": "turn-1", "worker": "w1", "thread": "t1", "after": "start"}],
        "schedule": schedule if schedule is not None else [HOLD, TAKE_OVER, RELEASE_AFTER],
    }
    data.update(changes)
    return data


def dies_at(op_id: str, *, exit_first: bool = False) -> Any:
    """Answers every run but `op_id`, at which it dies by SIGKILL, as its failpoint would."""

    async def behave(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run) and message.op_id == op_id:
            worker.die(-9, exit_first=exit_first)
        else:
            await obliging(worker, message)

    return behave


def held_then(on_release: str | None = "ok") -> Any:
    """Parks turn-1 at put.before occurrence 3; once released, answers with `on_release`, or
    never answers if it is None."""

    async def behave(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            worker.answer(Hit(hit_seq=7, fp="put.before", op_id=message.op_id, occurrence=3))
        elif isinstance(message, ReleaseMessage) and on_release is not None:
            worker.answer(Result(op_id="turn-1", status="ok", worker_seq=9))
        elif isinstance(message, Shutdown):
            worker.die(0)

    return behave


@pytest.mark.parametrize("exit_first", [False, True])
def test_a_killed_workers_op_is_taken_over_by_its_replacement(
    tmp_path: Path, exit_first: bool
) -> None:
    trial = Trial(tmp_path, k1a(), {"w1": dies_at("resume-1", exit_first=exit_first)})
    report = trial.run()
    assert report.violations == [] and report.problems == []
    assert report.fired_steps == [1] and report.unfired_steps == []
    resume = report.ops["resume-1"]
    assert (resume.outcome, resume.redelivered_to, resume.stale) == ("ok", "w2", ())
    events = trial.events()
    assert terminal_counts(events) == {"start-1": 1, "resume-1": 1}
    kinds = [f"{e.kind} {e.op_id or e.data.get('proc')}" for e in events]
    assert kinds.index("worker.exit w1") < kinds.index("dispatch.redeliver resume-1")
    assert kinds.count("client.invoke resume-1") == 1
    redeliver = next(e for e in events if e.kind == "dispatch.redeliver")
    assert redeliver.data == {"to": "w2", "from": "w1", "step": 1}
    assert redeliver.thread_id == "b-test/cell/1/t1"
    ok = next(e for e in events if e.kind == "client.ok" and e.op_id == "resume-1")
    assert ok.data["worker"] == "w2"
    [run] = [m for m in trial.workers["w2"].sent if isinstance(m, Run)]
    assert (run.op_id, run.thread_id, run.resume) == ("resume-1", "b-test/cell/1/t1", "ok")


def test_a_held_runs_op_is_taken_over_and_the_stale_run_released_after(tmp_path: Path) -> None:
    trial = Trial(tmp_path, k3(), {"w1": held_then("ok")})
    report = trial.run()
    assert report.violations == [] and report.problems == []
    assert report.fired_steps == [1, 2] and report.unfired_steps == []
    turn = report.ops["turn-1"]
    assert (turn.outcome, turn.redelivered_to) == ("ok", "w2")
    # The stale run's result came before shutdown: the trial waited for it.
    assert turn.stale == ("w1 answered ok",)
    events = trial.events()
    assert terminal_counts(events) == {"turn-1": 1}
    order = [e.kind for e in events if e.kind in {"dispatch.redeliver", "client.ok", "fp.release"}]
    assert order == ["dispatch.redeliver", "client.ok", "fp.release"]
    release = next(e for e in events if e.kind == "fp.release")
    assert release.data == {"proc": "w1", "hit_seq": 7, "barrier": "A", "step": 2}
    sent = [type(m).__name__ for m in trial.workers["w1"].sent]
    assert sent == ["ArmMessage", "Run", "ReleaseMessage", "Shutdown"]


def test_a_stale_run_never_released_is_left_held_and_noted(tmp_path: Path) -> None:
    trial = Trial(tmp_path, k3([HOLD, TAKE_OVER]), {"w1": held_then("ok")})
    report = trial.run()
    assert report.violations == [] and report.problems == []
    assert report.ops["turn-1"].stale == ("w1 gave no result before the trial ended",)
    assert not any(isinstance(m, ReleaseMessage) for m in trial.workers["w1"].sent)


def test_a_released_stale_run_that_never_answers_is_bounded_by_the_run_timeout(
    tmp_path: Path,
) -> None:
    data = k3(timeouts={"run_s": 0.05, "hello_s": 5, "shutdown_s": 5})
    trial = Trial(tmp_path, data, {"w1": held_then(None)})
    report = trial.run()
    assert report.ops["turn-1"].outcome == "ok"
    assert report.ops["turn-1"].stale == ("w1 gave no result in time",)
    assert report.problems == [
        "turn-1's stale run on w1 gave no result within 0.05 s of its release; what followed "
        "was ordered by a timer"
    ]


def test_a_redelivery_to_a_worker_that_is_gone_is_a_problem(tmp_path: Path) -> None:
    async def kills_w2_then_dies(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run) and message.op_id == "resume-1":
            trial.workers["w2"].die(1)
            worker.die(-9)
        else:
            await obliging(worker, message)

    trial = Trial(tmp_path, k1a(), {"w1": kills_w2_then_dies})
    report = trial.run()
    assert "step 1: w2 was not running when resume-1 was to be redelivered" in report.problems
    resume = report.ops["resume-1"]
    assert (resume.outcome, resume.redelivered_to) == ("info", None)
    assert not any(e.kind == "dispatch.redeliver" for e in trial.events())


def test_a_release_whose_barrier_is_no_longer_held_is_a_problem(tmp_path: Path) -> None:
    async def takes_over_and_kills_w1(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            trial.workers["w1"].die(-9)  # the stale worker dies while held
            worker.answer(Result(op_id=message.op_id, status="ok", worker_seq=3))
        elif isinstance(message, Shutdown):
            worker.die(0)

    trial = Trial(tmp_path, k3(), {"w1": held_then("ok"), "w2": takes_over_and_kills_w1})
    report = trial.run()
    assert report.ops["turn-1"].outcome == "ok"
    assert report.ops["turn-1"].stale == ("w1 exited before answering",)
    assert "step 2: A not held when turn-1 ended" in report.problems


def test_no_replacement_without_the_ending_fault(tmp_path: Path) -> None:
    # The failpoint never fires (the worker answers): the exit at shutdown fires nothing.
    trial = Trial(tmp_path, k1a(), {})
    report = trial.run()
    assert report.fired_steps == [] and report.unfired_steps == [1]
    assert report.ops["resume-1"].redelivered_to is None
    assert not any(e.kind == "dispatch.redeliver" for e in trial.events())


def test_a_result_from_a_worker_that_never_had_the_op_is_still_a_violation(tmp_path: Path) -> None:
    async def answers_twice(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            worker.answer(Result(op_id=message.op_id, status="ok", worker_seq=3))
            worker.answer(Result(op_id="start-1", status="ok", worker_seq=4))
        elif isinstance(message, Shutdown):
            worker.die(0)

    trial = Trial(tmp_path, k1a(), {"w1": dies_at("resume-1"), "w2": answers_twice})
    report = trial.run()
    assert report.violations == ["w2 sent a result for start-1, never sent to it"]
