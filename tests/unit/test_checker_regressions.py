"""Regressions from the review of the pre-freeze checker: each case once gave a wrong verdict, a
missed INVALID, or an exception."""

from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.checker import engine, freeze, status
from crashcourt.checker.artifacts import (
    ContextRule,
    LandmarkSpec,
    LandmarkTable,
    ProcessHistory,
    TrialArtifacts,
)
from crashcourt.spec.events import Event
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_engine import REGISTRY_SHA, outcomes, run
from tests.unit.test_checker_faults import trigger, with_scenario


def lost_put_ack_then_resume() -> TrialArtifacts:
    """start-1's put of the checkpoint after plan commits but raises (its ack lost); the run
    fails; one resume then starts approval from that checkpoint."""
    t = T.TrialBuilder()
    start, resume = t.op("start-1", "start"), t.op("resume-1", "resume", resume="approve")
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    w = t.workers["worker-1"]
    t.get_tuple(w, "start-1", "T1")
    c0 = t.put(w, "start-1", "T1", source="input", updates={"__start__": {"order": "T1"}})
    t.node(w, "start-1", "plan", t.task("T1", "plan"))
    t.put_writes(w, "start-1", "T1", c0, t.task("T1", "plan"), [("plan", "p1")])
    c1 = t.put(w, "start-1", "T1", source="loop", updates={"plan": "p1"}, returns=False)
    w.emit(
        "saver.put.raise",
        op_id="start-1",
        thread_id="T1",
        checkpoint_ns="",
        checkpoint_id=c1,
        parent_checkpoint_id=c0,
        status="fail",
        error="OperationalError: server closed the connection unexpectedly",
        data={"stage": "inner"},
    )
    t.fail("start-1", "worker-1", "lost ack")
    t.invoke(resume)
    t.dispatch("resume-1", "worker-1")
    t.w1_resume("worker-1", "resume-1", "T1", "approve")
    t.ok("resume-1", "worker-1")
    t.exit("worker-1")
    return t.build()


def test_i1a_does_not_pair_a_failed_put_with_a_later_run() -> None:
    got = outcomes(run(lost_put_ack_then_resume()))
    assert got["I1a"] == ("PASS", "LG-DUR-SYNC")


def test_i1a_flags_a_run_that_continues_after_its_put_failed() -> None:
    def fail_put_of_c1(events: list[Event]) -> list[Event]:
        returns = [e for e in events if e.kind == "saver.put.return" and e.op_id == "start-1"]
        target = returns[1]  # the checkpoint after plan; approval follows it in the same run
        return [
            e.model_copy(
                update={"kind": "saver.put.raise", "status": "fail", "error": "OperationalError: x"}
            )
            if e is target
            else e
            for e in events
        ]

    got = outcomes(run(M.edit_history(T.w1_trial(), "worker-1", fail_put_of_c1)))
    assert got["I1a"] == ("VIOLATION_CANDIDATE", "LG-DUR-SYNC")


def _drop_kill(artifacts: TrialArtifacts, *, returncode: int) -> TrialArtifacts:
    """The kill-mid-execute trial without its orchestrator kill, worker-1's exit replaced."""

    def edit(events: list[Event]) -> list[Event]:
        out: list[Event] = []
        for e in events:
            if e.kind in {"fault.inject", "fault.heal"}:
                continue
            if e.kind == "worker.exit" and e.data.get("proc") == "worker-1":
                e = e.model_copy(update={"data": {"proc": "worker-1", "returncode": returncode}})
            out.append(e)
        return out

    edited = M.edit_history(artifacts, "orchestrator", edit)
    return with_scenario(edited, faults=edited.scenario.faults[1:])


def test_an_unexplained_worker_exit_is_invalid() -> None:
    """Founder's rule of 2026-10-10: a worker that ends in a way no declared fault explains makes
    the trial INVALID (trigger 16), and so cannot witness a re-run permission either."""
    assert trigger(_drop_kill(T.kill_mid_execute_trial(), returncode=1)) == 16
    assert trigger(_drop_kill(T.kill_mid_execute_trial(), returncode=-11)) == 16


def test_an_undeclared_fault_or_hit_is_invalid() -> None:
    killed = T.kill_mid_execute_trial()
    assert trigger(with_scenario(killed, faults=killed.scenario.faults[1:])) == 9
    writes = T.kill_at_writes_trial("put_writes.before")
    assert trigger(with_scenario(writes, arms=(), faults=writes.scenario.faults[1:])) == 10


def _failed_effect(error: str) -> TrialArtifacts:
    killed = T.kill_mid_execute_trial()

    def edit(events: list[Event]) -> list[Event]:
        first = next(e for e in events if e.kind == "effect.response")
        return [
            e.model_copy(update={"status": "fail", "error": error, "data": {}}) if e is first else e
            for e in events
        ]

    return M.edit_history(killed, "worker-1", edit)


def test_only_a_connection_failure_of_a_dying_worker_is_excused() -> None:
    assert trigger(_failed_effect("HTTPError: 500 Internal Server Error")) == 6
    assert trigger(_failed_effect("ConnectionResetError: [Errno 104]")) != 6


def kill_then_second_resume() -> TrialArtifacts:
    """A kill while execute runs, the first resume lost (client.info), and a second resume op on
    another worker: two resumes on the thread."""
    t = T.TrialBuilder()
    start = t.op("start-1", "start")
    first, second = (
        t.op("resume-1", "resume", resume="approve"),
        t.op("resume-2", "resume", resume="approve"),
    )
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1")
    t.ok("start-1", "worker-1")
    t.invoke(first)
    t.dispatch("resume-1", "worker-1")
    t.w1_approve("worker-1", "resume-1", "T1", "approve")
    t.w1_execute("worker-1", "resume-1", "T1", "approve", end=False)
    t.exit("worker-1", returncode=-9, signal="SIGKILL")
    t.info("resume-1", "worker died")
    t.invoke(second)
    t.dispatch("resume-2", "worker-2")
    t.get_tuple(t.workers["worker-2"], "resume-2", "T1")
    _, attempt = t.w1_execute("worker-2", "resume-2", "T1", "approve")
    entry = ["charge:T1", attempt]
    t.w1_finish("worker-2", "resume-2", "T1", [entry], receipt=entry)
    t.ok("resume-2", "worker-2")
    t.exit("worker-2")
    return T.with_declared_kill(t.build())


def test_i7a_on_a_disclaimed_thread_is_gap() -> None:
    """I7: "Multi-writer or conflicting resume: GAP (LGP-DT)". I4 keeps its cause order."""
    got = outcomes(run(kill_then_second_resume()))
    assert got["I7a"] == ("GAP", None)
    assert got["I4"] == ("EXPECTED", "LG-DE-RERUN")


def test_records_the_checker_needs_are_checked() -> None:
    base = T.w1_trial()
    no_task = M.edit_history(
        base,
        "worker-1",
        lambda es: [
            e.model_copy(update={"task_id": None}) if e.kind == "node.end" else e for e in es
        ],
    )
    assert trigger(no_task) == 1

    def orphan_end(events: list[Event]) -> list[Event]:
        end = events[-1].model_copy(
            update={"kind": "node.end", "task_id": "task-none", "data": {"node": "x"}}
        )
        return [*events, end]

    assert trigger(M.edit_history(base, "worker-1", orphan_end)) == 3


def test_the_manifest_copy_is_hashed() -> None:
    base = T.w1_trial()
    assert base.manifest is not None
    edited = base.model_copy(
        update={"manifest": base.manifest.model_copy(update={"runner": "local", "git_dirty": True})}
    )
    assert trigger(edited) == 13


def test_sink_readings_need_a_source_hash_to_compare() -> None:
    assert trigger(with_scenario(T.w1_trial(), sink_source_sha256=None)) == 6


def test_a_silent_dispatched_worker_must_still_exit() -> None:
    base = T.w1_trial()
    started = M.edit_history(
        base,
        "orchestrator",
        lambda es: [
            es[0].model_copy(
                update={
                    "kind": "worker.start",
                    "op_id": None,
                    "logical_op": None,
                    "data": {"proc": "worker-9"},
                }
            ),
            *es,
        ],
    )
    with_history = started.model_copy(
        update={"histories": (*started.histories, ProcessHistory(proc="worker-9", events=()))}
    )
    assert trigger(with_history) == 7


# -- the freeze ---------------------------------------------------------------------------------

TABLE = LandmarkTable(
    name="DEMO-landmarks",
    status="draft",
    landmarks=(LandmarkSpec(id="L-X", method="put", rules=(ContextRule(rule="last_call"),)),),
)


def test_trial_verification_against_a_freeze_with_several_tables() -> None:
    other = TABLE.model_copy(update={"name": "DEMO-other"})
    text = freeze.render(freeze.compute(REGISTRY_SHA, [TABLE, other]))
    assert freeze.verify_trial(text, REGISTRY_SHA, TABLE).checker_sha256
    assert freeze.verify_trial(text, REGISTRY_SHA, None).checker_sha256
    with pytest.raises(freeze.FreezeMismatch):
        freeze.verify_trial(text, REGISTRY_SHA, TABLE.model_copy(update={"name": "DEMO-unfrozen"}))
    with pytest.raises(freeze.FreezeMismatch):
        freeze.verify_trial(text, "0" * 64, None)


def test_a_frozen_checker_classifies_and_stamps_its_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frozen = tmp_path / "FROZEN.md"
    frozen.write_text(freeze.render(freeze.compute(REGISTRY_SHA, [TABLE])), encoding="utf-8")
    monkeypatch.setattr(status, "FREEZE_STATE", "FROZEN")
    monkeypatch.setattr(engine, "FREEZE_STATE", "FROZEN")
    monkeypatch.setattr(engine, "FROZEN_PATH", frozen)
    out = run(T.w1_trial())
    assert out.report.freeze_state == "FROZEN"
    assert out.report.authorized_for_new_evidence is True
    assert out.report.frozen_sha256 == freeze.checker_sha256()
    # A checker that does not match its freeze refuses to classify.
    frozen.write_text(freeze.render(freeze.compute("0" * 64, [TABLE])), encoding="utf-8")
    with pytest.raises(freeze.FreezeMismatch):
        run(T.w1_trial())


@pytest.mark.usefixtures("pre_freeze")
def test_arms_need_landmarks_even_with_a_table_outside_synthetic_trials() -> None:
    base = T.kill_at_writes_trial("put_writes.before")
    arm = base.scenario.arms[0]
    control = T.as_run(with_scenario(base, arms=(arm,), landmarks=TABLE), evidence="control")
    assert trigger(control) == 11
