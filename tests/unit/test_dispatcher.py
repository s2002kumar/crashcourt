"""The trial engine's client view and barriers, driven by in-process fake workers.

A fake worker answers messages the way a script says; the engine cannot tell it from a process.
These tests cover what the protocol must survive: a duplicate result, a missing result (the
worker dies, or the run times out), a result for an op never sent, and an unknown message.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.orchestrator.dispatcher import (
    Disconnected,
    EngineReport,
    Exited,
    Note,
    Received,
    StartError,
    TrialEngine,
    describe_status,
)
from crashcourt.spec.events import Event
from crashcourt.spec.protocol import (
    ArmMessage,
    Hello,
    Hit,
    ReleaseMessage,
    Result,
    Run,
    Shutdown,
)
from crashcourt.spec.scenario import parse_scenario

RUN = RunIdentity("b-test", "cell", 1, "seed")

Behavior = Callable[["FakeWorker", BaseModel], Awaitable[None]]


class FakeWorker:
    def __init__(
        self,
        name: str,
        notes: asyncio.Queue[Note],
        history: HistoryWriter,
        behavior: Behavior,
        *,
        hello_error: str | None = None,
    ) -> None:
        self.name = name
        self.pid = 1000 + int(name[1:])
        self.notes = notes
        self.history = history
        self.behavior = behavior
        self.hello_error = hello_error
        self.sent: list[BaseModel] = []
        #: The orchestrator's last two events when each run left: they must be its invoke and send.
        self.logged_before_run: dict[str, list[str]] = {}
        self.killed: list[str] = []
        self.forced = 0
        self.alive = True
        history.emit("worker.start", data={"proc": name, "pid": self.pid})

    async def hello(self, timeout: float) -> None:
        if self.hello_error is not None:
            self.die(3)
            raise StartError(self.hello_error)

    async def send(self, message: BaseModel) -> None:
        if not self.alive:
            raise ConnectionError("gone")
        self.sent.append(message)
        if isinstance(message, Run):
            events = read_history(self.history.path).events[-2:]
            self.logged_before_run[message.op_id] = [f"{e.kind} {e.op_id}" for e in events]
        await self.behavior(self, message)

    async def kill(self, fault_id: str) -> None:
        self.killed.append(fault_id)
        self.history.emit("fault.inject", fault_id=fault_id, data={"fault": "kill"})
        self.die(-9)
        self.history.emit("fault.heal", fault_id=fault_id, data={"returncode": -9})

    def force_kill(self) -> None:
        self.forced += 1
        self.die(-9)

    def die(self, returncode: int, *, exit_first: bool = False) -> None:
        if not self.alive:
            return
        self.alive = False
        notes: list[Note] = [Disconnected(self.name), Exited(self.name, returncode)]
        for note in reversed(notes) if exit_first else notes:
            self.notes.put_nowait(note)

    def answer(self, message: Hello | Hit | Result) -> None:
        self.notes.put_nowait(Received(self.name, message))

    def runs(self) -> list[str]:
        return [m.op_id for m in self.sent if isinstance(m, Run)]


async def obliging(worker: FakeWorker, message: BaseModel) -> None:
    """Answers every run with ok, and exits on shutdown."""
    if isinstance(message, Run):
        worker.answer(Result(op_id=message.op_id, status="ok", output={"n": 1}, worker_seq=5))
    elif isinstance(message, Shutdown):
        worker.die(0)


BASE: dict[str, Any] = {
    "v": 1,
    "name": "fake",
    "cell": "cell",
    "seed": "seed",
    "workload": "smoke",
    "target": {"saver": "postgres", "driver": "async", "durability": "sync"},
    "landmarks": "langgraph-v1",
    "workers": ["w1", "w2"],
    "ops": [
        {"id": "op-1", "worker": "w1", "thread": "t1", "after": "start"},
        {"id": "op-2", "worker": "w2", "thread": "t2", "after": "start"},
        {"id": "op-3", "worker": "w2", "thread": "t1", "after": "op-1"},
    ],
    "timeouts": {"run_s": 5, "hello_s": 5, "shutdown_s": 5},
}


def scenario(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE)
    data.update(changes)
    return data


class Trial:
    def __init__(self, tmp_path: Path, data: dict[str, Any], behaviors: dict[str, Behavior]):
        self.path = tmp_path / "orchestrator.jsonl"
        self.history = HistoryWriter(self.path, run=RUN, proc="orchestrator")
        self.scenario = parse_scenario(data)
        self.behaviors = behaviors
        self.workers: dict[str, FakeWorker] = {}
        self.hello_errors: dict[str, str] = {}

    def spawn(self, name: str, notes: asyncio.Queue[Note]) -> FakeWorker:
        worker = FakeWorker(
            name,
            notes,
            self.history,
            self.behaviors.get(name, obliging),
            hello_error=self.hello_errors.get(name),
        )
        self.workers[name] = worker
        return worker

    def run(self) -> EngineReport:
        engine = TrialEngine(
            self.scenario, history=self.history, thread_prefix="b-test/cell/1", spawn=self.spawn
        )
        report = asyncio.run(engine.run())
        self.history.close()
        return report

    def events(self) -> tuple[Event, ...]:
        return read_history(self.path).events

    def kinds(self, prefix: str = "client.") -> list[str]:
        return [f"{e.kind} {e.op_id}" for e in self.events() if e.kind.startswith(prefix)]


def terminal_counts(events: tuple[Event, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        if e.kind in ("client.ok", "client.fail", "client.info") and e.op_id:
            counts[e.op_id] = counts.get(e.op_id, 0) + 1
    return counts


def test_every_invoke_gets_exactly_one_terminal_event(tmp_path: Path) -> None:
    trial = Trial(tmp_path, scenario(), {})
    report = trial.run()
    assert report.violations == [] and report.problems == []
    assert terminal_counts(trial.events()) == {"op-1": 1, "op-2": 1, "op-3": 1}
    kinds = trial.kinds()
    # op-3 runs after op-1, so it is invoked only once op-1 has its terminal event.
    assert kinds.index("client.invoke op-3") > kinds.index("client.ok op-1")
    for op in ("op-1", "op-2", "op-3"):
        worker = trial.workers[report.ops[op].worker]
        assert worker.logged_before_run[op] == [f"client.invoke {op}", f"dispatch.send {op}"]
    assert {op: r.outcome for op, r in report.ops.items()} == dict.fromkeys(report.ops, "ok")


def test_the_client_event_names_the_worker_and_its_last_event(tmp_path: Path) -> None:
    trial = Trial(tmp_path, scenario(), {})
    trial.run()
    ok = next(e for e in trial.events() if e.kind == "client.ok" and e.op_id == "op-1")
    assert ok.data == {"worker": "w1", "worker_seq": 5, "output": {"n": 1}}
    assert ok.thread_id == "b-test/cell/1/t1" and ok.status == "ok"


def test_every_worker_is_shut_down_and_its_exit_logged(tmp_path: Path) -> None:
    trial = Trial(tmp_path, scenario(), {})
    report = trial.run()
    for worker in trial.workers.values():
        assert isinstance(worker.sent[-1], Shutdown)
    exits = [e for e in trial.events() if e.kind == "worker.exit"]
    assert sorted(str(e.data["proc"]) for e in exits) == ["w1", "w2"]
    assert all(e.data["exit_code"] == 0 and e.data["signal"] is None for e in exits)
    assert all(w.shutdown_sent and w.returncode == 0 for w in report.workers.values())


def test_a_duplicate_result_is_a_violation_and_not_a_second_terminal(tmp_path: Path) -> None:
    async def twice(worker: FakeWorker, message: BaseModel) -> None:
        await obliging(worker, message)
        if isinstance(message, Run):
            worker.answer(Result(op_id=message.op_id, status="ok"))

    trial = Trial(tmp_path, scenario(), {"w1": twice})
    report = trial.run()
    assert terminal_counts(trial.events())["op-1"] == 1
    assert report.violations == ["w1 sent a second result for op-1"]


def test_a_result_for_an_op_never_sent_is_a_violation(tmp_path: Path) -> None:
    async def stray(worker: FakeWorker, message: BaseModel) -> None:
        await obliging(worker, message)
        if isinstance(message, Run):
            worker.answer(Result(op_id="op-2", status="ok"))  # op-2 went to w2

    trial = Trial(tmp_path, scenario(), {"w1": stray})
    report = trial.run()
    assert "w1 sent a result for op-2, never sent to it" in report.violations
    assert terminal_counts(trial.events()) == {"op-1": 1, "op-2": 1, "op-3": 1}


@pytest.mark.parametrize("exit_first", [False, True])
def test_a_missing_result_from_a_dead_worker_is_info_never_fail(
    tmp_path: Path, exit_first: bool
) -> None:
    async def dies(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            worker.die(-9, exit_first=exit_first)

    trial = Trial(tmp_path, scenario(), {"w1": dies})
    report = trial.run()
    events = trial.events()
    info = next(e for e in events if e.kind == "client.info" and e.op_id == "op-1")
    assert "SIGKILL" in str(info.data["reason"]) and info.status == "info"
    assert not any(e.kind == "client.fail" for e in events)
    exit_w1 = next(e for e in events if e.kind == "worker.exit" and e.data["proc"] == "w1")
    assert exit_w1.data["signal"] == "SIGKILL" and exit_w1.data["returncode"] == -9
    # The info comes after the exit is known, so it can say how the worker ended.
    assert info.seq > exit_w1.seq
    assert report.ops["op-1"].outcome == "info"
    # op-3 runs after op-1 on w2, which is alive: it is still sent.
    assert report.ops["op-3"].outcome == "ok"


def test_an_op_for_a_dead_worker_is_never_invoked(tmp_path: Path) -> None:
    data = scenario()
    data["ops"][2]["worker"] = "w1"  # op-3 after op-1, both on w1

    async def dies(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            worker.die(-9)

    trial = Trial(tmp_path, data, {"w1": dies})
    report = trial.run()
    assert "client.invoke op-3" not in trial.kinds()
    assert report.ops["op-3"].sent is False and report.ops["op-3"].outcome is None
    assert "w1 was no longer running" in str(report.ops["op-3"].reason)


def test_a_run_past_its_timeout_is_info_and_the_trial_still_ends(tmp_path: Path) -> None:
    pending: list[str] = []

    async def silent(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            pending.append(message.op_id)  # never answered while the client waits
        elif isinstance(message, Shutdown):
            for op in pending:
                worker.answer(Result(op_id=op, status="ok"))  # too late
            worker.die(0)

    data = scenario(timeouts={"run_s": 0.05, "hello_s": 5, "shutdown_s": 5})
    trial = Trial(tmp_path, data, {"w1": silent})
    report = trial.run()
    info = next(e for e in trial.events() if e.kind == "client.info" and e.op_id == "op-1")
    assert info.data["reason"] == "no result within 0.05 s"
    assert terminal_counts(trial.events())["op-1"] == 1
    assert report.ops["op-1"].late_result == "ok"
    assert report.violations == [] and report.ops["op-3"].outcome == "ok"
    # The run was still going when the client gave up, so op-3 (same thread) raced it: the
    # trial was ordered by a timer and cannot be trusted.
    assert report.problems == [
        "op-1 passed its timeout while running, not parked at a barrier; what followed was "
        "ordered by a timer"
    ]


def parks_then_answers(hit_seq: int, fp: str = "get_tuple.after") -> Behavior:
    """Parks its first run; answers it once released and every later run at once."""

    async def behave(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            if len([m for m in worker.sent if isinstance(m, Run)]) == 1:
                worker.answer(Hit(hit_seq=hit_seq, fp=fp, op_id=message.op_id, occurrence=1))  # type: ignore[arg-type]
            else:
                worker.answer(Result(op_id=message.op_id, status="ok", worker_seq=1))
        elif isinstance(message, ReleaseMessage):
            run = next(m for m in worker.sent if isinstance(m, Run))
            worker.answer(Result(op_id=run.op_id, status="ok", worker_seq=1))
        elif isinstance(message, Shutdown):
            worker.die(0)

    return behave


def test_releasing_a_run_after_its_client_gave_up_is_a_problem(tmp_path: Path) -> None:
    # op-1 times out parked at B1; op-3 is sent by the timer; then B2's hit releases B1, and
    # op-1 resumes beside op-3 on thread t1. Had op-2 parked sooner, op-3 would have waited.
    data = scenario(schedule=BARRIERS, timeouts={"run_s": 0.05, "hello_s": 5, "shutdown_s": 5})

    async def parks_late(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run) and message.op_id == "op-3":
            # op-2 reaches its barrier only now, after op-1's timeout has sent op-3.
            worker.answer(Hit(hit_seq=4, fp="get_tuple.after", op_id="op-2", occurrence=1))
            worker.answer(Result(op_id="op-3", status="ok", worker_seq=5))
        elif isinstance(message, ReleaseMessage):
            worker.answer(Result(op_id="op-2", status="ok", worker_seq=6))
        elif isinstance(message, Shutdown):
            worker.die(0)

    trial = Trial(tmp_path, data, {"w1": parks_then_answers(1), "w2": parks_late})
    report = trial.run()
    assert report.fired_steps == [2]
    assert any("released op-1 after its client gave up" in p for p in report.problems)


def test_a_timeout_on_a_run_parked_for_good_orders_nothing(tmp_path: Path) -> None:
    data = scenario(schedule=[BARRIERS[0]], timeouts={"run_s": 0.05, "hello_s": 5, "shutdown_s": 5})
    trial = Trial(tmp_path, data, {"w1": parks(1)})
    report = trial.run()
    assert report.ops["op-1"].outcome == "info" and report.problems == []
    assert report.ops["op-3"].outcome == "ok"


def test_a_failed_result_is_fail_with_its_error(tmp_path: Path) -> None:
    async def fails(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            worker.answer(
                Result(op_id=message.op_id, status="fail", error="E: x", error_type="m.E")
            )
        else:
            await obliging(worker, message)

    trial = Trial(tmp_path, scenario(), {"w1": fails})
    trial.run()
    fail = next(e for e in trial.events() if e.kind == "client.fail")
    assert (fail.op_id, fail.status, fail.error) == ("op-1", "fail", "E: x")
    # No worker_seq in the result, so no worker either: the two go together.
    assert fail.data == {"error_type": "m.E"}


def test_a_worker_that_will_not_die_is_given_up_on(tmp_path: Path) -> None:
    async def undying(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            worker.answer(Result(op_id=message.op_id, status="ok", worker_seq=1))

    def ignore_kill() -> None:
        trial.workers["w2"].forced += 1

    data = scenario(timeouts={"run_s": 5, "hello_s": 5, "shutdown_s": 0.05})
    trial = Trial(tmp_path, data, {"w2": undying})
    original = trial.spawn

    def spawn(name: str, notes: asyncio.Queue[Note]) -> FakeWorker:
        worker = original(name, notes)
        if name == "w2":
            worker.force_kill = ignore_kill  # type: ignore[method-assign]
        return worker

    trial.spawn = spawn  # type: ignore[method-assign]
    report = trial.run()
    assert report.problems == [
        "w2 did not exit within 0.05 s of shutdown",
        "w2 did not exit even after SIGKILL",
    ]


BARRIERS = [
    {
        "on": {"fp": "get_tuple.after", "worker": "w1", "op": "op-1"},
        "do": {"pause": "B1"},
        "landmark": "L-GET",
    },
    {
        "on": {"fp": "get_tuple.after", "worker": "w2", "op": "op-2"},
        "do": {"pause": "B2"},
        "landmark": "L-GET",
    },
    {"on": {"all_paused": ["B1", "B2"]}, "do": {"release": ["B1", "B2"]}},
]


def parks(hit_seq: int) -> Behavior:
    """Reports a hit for each run, answers once released, exits on shutdown."""

    async def behave(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            parked = [m for m in worker.sent if isinstance(m, Run)]
            if len(parked) == 1:
                worker.answer(
                    Hit(hit_seq=hit_seq, fp="get_tuple.after", op_id=message.op_id, occurrence=1)
                )
            else:
                worker.answer(Result(op_id=message.op_id, status="ok"))
        elif isinstance(message, ReleaseMessage):
            run = next(m for m in worker.sent if isinstance(m, Run))
            worker.answer(Result(op_id=run.op_id, status="ok"))
        elif isinstance(message, Shutdown):
            worker.die(0)

    return behave


def test_arms_are_sent_before_any_run(tmp_path: Path) -> None:
    trial = Trial(tmp_path, scenario(schedule=BARRIERS), {"w1": parks(1), "w2": parks(4)})
    trial.run()
    for name, op in (("w1", "op-1"), ("w2", "op-2")):
        sent = trial.workers[name].sent
        assert isinstance(sent[0], ArmMessage) and sent[0].op_id == op
        assert isinstance(sent[1], Run)


def test_both_barriers_are_held_before_either_is_released(tmp_path: Path) -> None:
    trial = Trial(tmp_path, scenario(schedule=BARRIERS), {"w1": parks(1), "w2": parks(4)})
    report = trial.run()
    releases = [e for e in trial.events() if e.kind == "fp.release"]
    assert [(e.data["proc"], e.data["hit_seq"], e.data["barrier"]) for e in releases] == [
        ("w1", 1, "B1"),
        ("w2", 4, "B2"),
    ]
    assert report.fired_steps == [2] and report.unfired_steps == []
    for name, seq in (("w1", 1), ("w2", 4)):
        assert ReleaseMessage(hit_seq=seq) in trial.workers[name].sent
    assert terminal_counts(trial.events()) == {"op-1": 1, "op-2": 1, "op-3": 1}


def test_one_held_barrier_releases_nothing(tmp_path: Path) -> None:
    data = scenario(schedule=BARRIERS, timeouts={"run_s": 0.05, "hello_s": 5, "shutdown_s": 5})

    async def never_parks(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Shutdown):
            worker.die(0)

    trial = Trial(tmp_path, data, {"w1": parks(1), "w2": never_parks})
    report = trial.run()
    assert not any(e.kind == "fp.release" for e in trial.events())
    assert report.unfired_steps == [2]
    assert report.ops["op-1"].outcome == "info" and report.ops["op-2"].outcome == "info"
    assert any(p.startswith("op-2 passed its timeout while running") for p in report.problems)


def test_a_kill_step_kills_the_parked_worker_and_its_op_is_info(tmp_path: Path) -> None:
    data = scenario(
        schedule=[
            BARRIERS[0],
            {"on": {"all_paused": ["B1"]}, "do": {"kill": "w1"}},
        ]
    )
    trial = Trial(tmp_path, data, {"w1": parks(1)})
    report = trial.run()
    assert trial.workers["w1"].killed == ["step-1"]
    assert report.workers["w1"].killed_by_step == "step-1"
    assert report.ops["op-1"].outcome == "info"
    kinds = [e.kind for e in trial.events()]
    assert kinds.index("fault.inject") < kinds.index("fault.heal")


def test_a_hit_no_step_pauses_is_a_violation(tmp_path: Path) -> None:
    trial = Trial(
        tmp_path,
        scenario(timeouts={"run_s": 0.05, "hello_s": 5, "shutdown_s": 5}),
        {"w1": parks(1)},
    )
    report = trial.run()
    assert any("where no step pauses" in v for v in report.violations)


def test_a_protocol_violation_drops_and_kills_the_worker(tmp_path: Path) -> None:
    async def garbles(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            worker.notes.put_nowait(
                Disconnected(worker.name, violation="the worker sent an invalid 'gossip' message")
            )

    trial = Trial(tmp_path, scenario(), {"w1": garbles})
    report = trial.run()
    assert report.violations == ["w1: the worker sent an invalid 'gossip' message"]
    assert trial.workers["w1"].forced == 1 and report.workers["w1"].forced
    assert report.ops["op-1"].outcome == "info"


def test_a_second_hello_is_a_violation(tmp_path: Path) -> None:
    async def greets(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            worker.answer(Hello(worker=worker.name, pid=worker.pid))
        await obliging(worker, message)

    trial = Trial(tmp_path, scenario(), {"w1": greets})
    assert "w1 sent hello twice" in trial.run().violations


def test_a_worker_that_does_not_start_ends_the_trial_before_any_invoke(tmp_path: Path) -> None:
    trial = Trial(tmp_path, scenario(), {})
    trial.hello_errors["w2"] = "exited with status 3 before connecting"
    report = trial.run()
    assert report.problems == ["w2 did not start: exited with status 3 before connecting"]
    assert trial.kinds() == []
    assert isinstance(trial.workers["w1"].sent[-1], Shutdown)
    assert all(e.data["proc"] in {"w1", "w2"} for e in trial.events() if e.kind == "worker.exit")


def test_a_worker_that_ignores_shutdown_is_killed_and_the_trial_says_so(tmp_path: Path) -> None:
    async def stubborn(worker: FakeWorker, message: BaseModel) -> None:
        if isinstance(message, Run):
            await obliging(worker, message)  # answers, but never exits on shutdown

    data = scenario(timeouts={"run_s": 5, "hello_s": 5, "shutdown_s": 0.05})
    trial = Trial(tmp_path, data, {"w2": stubborn})
    report = trial.run()
    assert trial.workers["w2"].forced == 1
    assert report.problems == ["w2 did not exit within 0.05 s of shutdown"]


def test_status_descriptions() -> None:
    assert describe_status(0) == {"returncode": 0, "exit_code": 0, "signal": None}
    assert describe_status(3)["exit_code"] == 3
    assert describe_status(-9) == {"returncode": -9, "exit_code": None, "signal": "SIGKILL"}
    assert describe_status(-200)["signal"] == "signal 200"
