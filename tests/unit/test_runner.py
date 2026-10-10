"""The runner's preflight refusals, and how it judges whether a trial can be trusted as evidence."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from crashcourt.cli import main
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.orchestrator import runner
from crashcourt.orchestrator.dispatcher import EngineReport, OpReport, WorkerReport
from crashcourt.orchestrator.runner import (
    CANONICAL_CLOCK_LIMIT_NS,
    RunRefused,
    assess,
    check_clock,
    check_sink,
    require_linux,
    require_local_database,
)
from crashcourt.sink.ledger import LEDGER_VERSION
from crashcourt.spec.events import Event
from crashcourt.spec.scenario import Scenario, parse_scenario

# -- preflight --------------------------------------------------------------------------------


def test_run_refuses_a_host_without_posix_signals_and_points_windows_to_wsl2() -> None:
    with pytest.raises(RunRefused, match="WSL2"):
        require_linux("win32")
    with pytest.raises(RunRefused, match="Linux container"):
        require_linux("darwin")
    require_linux("linux")


def test_the_cli_says_so_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def windows() -> None:
        require_linux("win32")

    monkeypatch.setattr(runner, "require_linux", windows)
    assert main(["run", "scenarios/smoke.yaml", "--trials", "5"]) == 2
    assert "run refused" in capsys.readouterr().out


def test_a_database_off_this_host_needs_its_owner_to_say_so() -> None:
    with pytest.raises(RunRefused, match="--i-own-this-database"):
        require_local_database("postgresql://u:p@db.example.com:5432/x", own_database=False)
    require_local_database("postgresql://u:p@db.example.com:5432/x", own_database=True)
    require_local_database("postgresql://u:p@127.0.0.1:55432/x", own_database=False)


def test_an_ordinary_run_records_a_clock_offset_beyond_the_limit_as_a_warning() -> None:
    assert check_clock({"manifest": 0, "batch start": 2_000}, canonical=False) is None
    far = CANONICAL_CLOCK_LIMIT_NS + 1
    warning = check_clock({"manifest": far, "batch start": 0}, canonical=False)
    assert warning is not None and "beyond the 1 s limit" in warning
    assert "never used to order events" in warning


@pytest.mark.parametrize(
    "offsets",
    [
        {"manifest": CANONICAL_CLOCK_LIMIT_NS + 1, "batch start": 0},
        {"manifest": 0, "batch start": -CANONICAL_CLOCK_LIMIT_NS - 1},
        {"manifest": None, "batch start": 0},
        # Docker Desktop's VM on Windows, as measured on 2026-10-09: 830.7 s behind the host.
        {"manifest": -830_700_000_000, "batch start": -830_700_000_000},
    ],
)
def test_a_canonical_batch_fails_closed_beyond_the_limit(offsets: dict[str, int | None]) -> None:
    with pytest.raises(RunRefused, match="canonical batch refused"):
        check_clock(offsets, canonical=True)


def test_a_canonical_batch_accepts_an_offset_at_the_limit() -> None:
    limit = CANONICAL_CLOCK_LIMIT_NS
    assert check_clock({"manifest": limit, "batch start": -limit}, canonical=True) is None


GOOD_SINK: dict[str, object] = {
    "status": "ok",
    "ledger_version": LEDGER_VERSION,
    "test_hooks": False,
    "resume_error": None,
    "source_sha256": "a" * 64,
}


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"test_hooks": True}, "a test hook is armed"),
        ({"source_sha256": "b" * 64}, "serves source bbbbbbbbbbbb"),
        ({"ledger_version": 1}, "ledger version 1"),
        ({"status": "starting"}, "status starting"),
        ({"resume_error": "another trial"}, "could not resume"),
    ],
)
def test_the_sink_must_be_fit_for_a_batch(change: dict[str, object], problem: str) -> None:
    assert check_sink(GOOD_SINK, "a" * 64) == []
    assert any(problem in p for p in check_sink({**GOOD_SINK, **change}, "a" * 64))


def test_batch_ids_are_path_safe_and_never_demo() -> None:
    assert runner._batch_id("b-1").startswith("b-")  # pyright: ignore[reportPrivateUsage]
    assert runner._batch_id(None).startswith("b-")  # pyright: ignore[reportPrivateUsage]
    for bad in ("../x", "a b", "DEMO-1"):
        with pytest.raises(RunRefused):
            runner._batch_id(bad)  # pyright: ignore[reportPrivateUsage]


# -- assessing a trial ------------------------------------------------------------------------

RUN = RunIdentity("b-1", "smoke", 1, "seed")
DATA: dict[str, Any] = {
    "v": 1,
    "name": "a",
    "cell": "smoke",
    "seed": "s",
    "workload": "smoke",
    "target": {"saver": "postgres", "driver": "async", "durability": "sync"},
    "workers": ["w1"],
    "ops": [{"id": "op-1", "worker": "w1", "thread": "t1", "after": "start"}],
}


def scenario(schedule: list[dict[str, Any]] | None = None) -> Scenario:
    data = copy.deepcopy(DATA)
    data["schedule"] = schedule or []
    return parse_scenario(data)


class TrialFiles:
    """Writes a trial's histories the way the orchestrator and a worker would."""

    def __init__(self, tmp_path: Path) -> None:
        self.dir = tmp_path / "trial-00001"
        self.dir.mkdir()
        self.orch = HistoryWriter(self.dir / "orchestrator.jsonl", run=RUN, proc="orchestrator")
        self.w1 = HistoryWriter(self.dir / "w1.jsonl", run=RUN, proc="w1", pid=200)

    def normal(self, *, terminal: str = "client.ok", returncode: int = 0) -> None:
        self.orch.emit("worker.start", data={"proc": "w1", "pid": 200})
        self.orch.emit("client.invoke", op_id="op-1", data={"worker": "w1"})
        self.orch.emit("dispatch.send", op_id="op-1", data={"to": "w1"})
        self.w1.emit("saver.get_tuple.call", op_id="op-1")
        last = self.w1.emit("saver.get_tuple.return", op_id="op-1", status="ok")
        data: dict[str, Any] = {"worker": "w1", "worker_seq": last.seq}
        if terminal == "client.info":
            data = {"worker": "w1", "reason": "x"}
        if terminal:
            self.orch.emit(terminal, op_id="op-1", status=terminal.split(".")[1], data=data)  # type: ignore[arg-type]
        self.close()
        status = {"returncode": returncode, "exit_code": returncode, "signal": None}
        if returncode < 0:
            status = {"returncode": returncode, "exit_code": None, "signal": "SIGKILL"}
        self.append_exit(status)

    def append_exit(self, status: dict[str, Any]) -> None:
        """worker.exit, written after the worker's history is closed, as waitpid would report it."""
        events = read_history(self.dir / "orchestrator.jsonl").events
        exit_event = Event(
            v=1,
            batch=RUN.batch,
            cell=RUN.cell,
            trial=RUN.trial,
            seed=RUN.seed,
            proc="orchestrator",
            pid=events[-1].pid,
            seq=len(events),
            mono_ns=events[-1].mono_ns + 1,
            wall_ns=events[-1].wall_ns + 1,
            kind="worker.exit",
            data={"proc": "w1", "pid": 200, **status},
        )
        with (self.dir / "orchestrator.jsonl").open("ab") as handle:
            handle.write(exit_event.to_line().encode())

    def close(self) -> None:
        self.orch.close()
        self.w1.close()


def report(
    *, returncode: int = 0, shutdown_sent: bool = True, killed_by_step: str | None = None
) -> EngineReport:
    return EngineReport(
        ops={"op-1": OpReport("w1", "b-1/smoke/1/t1", True, "ok", None, None)},
        workers={"w1": WorkerReport(200, returncode, shutdown_sent, killed_by_step, False)},
        fired_steps=[],
        unfired_steps=[],
    )


def test_a_trial_that_ran_as_declared_is_valid_and_its_merged_history_is_written(
    tmp_path: Path,
) -> None:
    files = TrialFiles(tmp_path)
    files.normal()
    result = assess(scenario(), report(), files.dir, [])
    assert result.valid, result.reasons
    assert result.outcomes == {"ok": 1}
    merged = (files.dir / "merged.jsonl").read_text().splitlines()
    assert len(merged) == 7  # five orchestrator events and the worker's two


def test_an_invoke_without_a_terminal_event_makes_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal(terminal="")
    result = assess(scenario(), report(), files.dir, [])
    assert not result.valid
    assert "op op-1 has 0 terminal events, not exactly one" in result.reasons


def test_two_terminal_events_make_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.orch.emit("client.info", op_id="op-1", status="info", data={"reason": "x"})
    files.normal()
    result = assess(scenario(), report(), files.dir, [])
    assert "op op-1 has 2 terminal events, not exactly one" in result.reasons


def test_a_step_that_never_fired_makes_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal()
    steps = [{"on": {"fp": "put.before", "worker": "w1", "op": "op-1"}, "do": {"exit": 3}}]
    result = assess(scenario(steps), report(), files.dir, [])
    assert "step 0 never fired (put.before in w1)" in result.reasons


def test_a_failpoint_that_could_not_act_makes_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.w1.emit("fp.hit", op_id="op-1", fp="put.before", status="fail", error="no barrier")
    files.normal()
    result = assess(scenario(), report(), files.dir, [])
    assert any("a failpoint could not act: no barrier" in r for r in result.reasons)


def test_an_exit_the_schedule_does_not_explain_makes_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal(terminal="client.info", returncode=1)
    result = assess(scenario(), report(returncode=1, shutdown_sent=False), files.dir, [])
    assert not result.valid
    assert result.reasons == ["w1 ended status 1, which the schedule does not explain"]


def test_an_oom_kill_is_not_explained_by_the_schedule(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal(terminal="client.info", returncode=-9)
    result = assess(scenario(), report(returncode=-9, shutdown_sent=False), files.dir, [])
    assert result.reasons == ["w1 ended SIGKILL, which the schedule does not explain"]


def test_a_harness_exit_makes_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal(terminal="client.info", returncode=2)
    result = assess(scenario(), report(returncode=2, shutdown_sent=False), files.dir, [])
    assert "w1 exited 2: the orchestrator broke the control protocol" in result.reasons


def test_an_exit_armed_at_a_failpoint_explains_the_status(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    hit = files.w1.emit(
        "fp.hit", op_id="op-1", fp="put.before", data={"action": "exit", "occurrence": 1}
    )
    files.w1.emit("fp.action", op_id="op-1", fp="put.before", data={"hit_seq": hit.seq})
    files.normal(terminal="client.info", returncode=3)
    steps = [{"on": {"fp": "put.before", "worker": "w1", "op": "op-1"}, "do": {"exit": 3}}]
    result = assess(scenario(steps), report(returncode=3, shutdown_sent=False), files.dir, [])
    assert result.valid, result.reasons


def test_an_exit_status_other_than_the_armed_one_makes_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    hit = files.w1.emit(
        "fp.hit", op_id="op-1", fp="put.before", data={"action": "exit", "occurrence": 1}
    )
    files.w1.emit("fp.action", op_id="op-1", fp="put.before", data={"hit_seq": hit.seq})
    files.normal(terminal="client.info", returncode=1)
    steps = [{"on": {"fp": "put.before", "worker": "w1", "op": "op-1"}, "do": {"exit": 3}}]
    result = assess(scenario(steps), report(returncode=1, shutdown_sent=False), files.dir, [])
    assert "w1: its failpoint action implies 3, not 1" in result.reasons


def test_a_scheduled_kill_must_end_in_sigkill(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal(terminal="client.info", returncode=-9)
    killed = report(returncode=-9, shutdown_sent=False, killed_by_step="step-1")
    assert assess(scenario(), killed, files.dir, []).valid
    (tmp_path / "other").mkdir()
    files = TrialFiles(tmp_path / "other")
    files.normal(terminal="client.info", returncode=1)
    wrong = report(returncode=1, shutdown_sent=False, killed_by_step="step-1")
    reasons = assess(scenario(), wrong, files.dir, []).reasons
    assert "w1: killed by step-1 but ended status 1" in reasons


def test_calls_in_flight_when_a_step_ended_the_worker_are_recorded(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.w1.emit("saver.put.call", op_id="op-1")
    files.w1.emit("saver.put_writes.call", op_id="op-1")
    files.normal(terminal="client.info", returncode=-9)
    killed = report(returncode=-9, shutdown_sent=False, killed_by_step="step-1")
    result = assess(scenario(), killed, files.dir, [])
    assert result.valid
    assert result.observations == [
        "w1 ended with saver calls in flight: put (op-1), put_writes (op-1)"
    ]


def test_a_partial_line_in_the_orchestrators_history_makes_the_trial_invalid(
    tmp_path: Path,
) -> None:
    files = TrialFiles(tmp_path)
    files.normal()
    with (files.dir / "orchestrator.jsonl").open("ab") as handle:
        handle.write(b'{"v":1,"batch"')
    result = assess(scenario(), report(), files.dir, [])
    assert "the orchestrator's history ends in a partial line" in result.reasons


def test_an_unparseable_line_makes_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal()
    with (files.dir / "w1.jsonl").open("ab") as handle:
        handle.write(b"garbage\n")
    result = assess(scenario(), report(), files.dir, [])
    assert any("line 3 is not a valid event" in r for r in result.reasons)


def test_harness_problems_and_violations_make_the_trial_invalid(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal()
    engine = report()
    engine.violations.append("w1 sent a second result for op-1")
    engine.problems.append("w1 did not exit within 30 s of shutdown")
    result = assess(scenario(), engine, files.dir, [])
    assert result.reasons == [
        "protocol: w1 sent a second result for op-1",
        "w1 did not exit within 30 s of shutdown",
    ]


def test_batch_files_show_paths_without_a_home_directory(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    (root / "scenarios").mkdir(parents=True)
    shown = runner._shown  # pyright: ignore[reportPrivateUsage]
    assert shown(root / "scenarios" / "smoke.yaml", root) == "scenarios/smoke.yaml"
    assert shown(tmp_path / "elsewhere" / "mine.yaml", root) == "mine.yaml"
