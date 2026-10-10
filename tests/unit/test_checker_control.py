"""Trial control and provenance (founder's rules of 2026-10-10).

INVALID: uncontrolled timing, an unexplained worker death, missing causal evidence, an impossible
history, mismatched provenance, an incomplete effect history. Not INVALID: a controlled fault's
consequences, such as a killed worker's in-flight saver call that commits; the resulting history
is evaluated. And client.fail is observational: it says the client received an error, never that
the run changed nothing.
"""

from __future__ import annotations

from crashcourt.checker.artifacts import (
    ArmSpec,
    CheckpointRow,
    FaultSpec,
    HarnessRecord,
    TrialArtifacts,
)
from crashcourt.spec.events import Event
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_engine import outcomes, run
from tests.unit.test_checker_faults import trigger, with_scenario


def kill_while_parked(*, in_flight_lands: bool) -> TrialArtifacts:
    """resume-1 is held at put.before of the checkpoint after approval, with the approval task's
    put_writes still in flight beside it (LangGraph writes a step's pending writes beside its
    checkpoint); the orchestrator kills the worker at the barrier. With `in_flight_lands`, the
    in-flight writes commit between the two snapshots."""
    t = T.TrialBuilder()
    start, resume = t.op("start-1", "start"), t.op("resume-1", "resume", resume="approve")
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1")
    t.ok("start-1", "worker-1")
    t.invoke(resume)
    t.dispatch("resume-1", "worker-1")
    w = t.workers["worker-1"]
    t.get_tuple(w, "resume-1", "T1")
    c1 = str(t.graphs["T1"].head)
    approval = t.task("T1", "approval")
    t.put_writes(
        w, "resume-1", "T1", c1, "00000000-0000-0000-0000-000000000000", [("__resume__", "approve")]
    )
    t.node(w, "resume-1", "approval", approval)
    writes: list[tuple[str, object]] = [("__resume__", ["approve"]), ("decision", "approve")]
    w.emit(
        "saver.put_writes.call",
        op_id="resume-1",
        thread_id="T1",
        checkpoint_ns="",
        checkpoint_id=c1,
        task_id=approval,
        data={"channels": [c for c, _ in writes], "task_path": ""},
    )
    cp = "1f0affff-0000-6000-8000-00000000ffff"
    fields = {
        "thread_id": "T1",
        "checkpoint_ns": "",
        "checkpoint_id": cp,
        "parent_checkpoint_id": c1,
    }
    w.emit(
        "saver.put.call",
        op_id="resume-1",
        **fields,
        data={"step": 1, "source": "loop", "channels": ["decision"]},
    )
    t.fp(w, "put.before", "resume-1", "pause", **fields)
    t.arms.append(ArmSpec(point="put.before", action="pause", worker="worker-1", op_id="resume-1"))
    t.inject("kill-1", fault="kill", signal="SIGKILL", proc="worker-1")
    t.exit("worker-1", returncode=-9, signal="SIGKILL")
    t.heal("kill-1", returncode=-9)
    t.info("resume-1", "worker killed")
    t.faults.append(FaultSpec(kind="sigkill", fault_id="kill-1", worker="worker-1"))
    first = t.snapshot()
    if in_flight_lands:
        t.commit_writes("T1", c1, approval, writes)
    artifacts = t.build()
    assert artifacts.snapshots is not None
    pair = artifacts.snapshots.model_copy(update={"first": first})
    return artifacts.model_copy(update={"snapshots": pair})


def test_a_kill_at_a_barrier_is_controlled() -> None:
    out = run(kill_while_parked(in_flight_lands=False))
    assert out.report.invalid is None


def test_an_in_flight_write_that_lands_is_evaluated_not_invalidated() -> None:
    out = run(kill_while_parked(in_flight_lands=True))
    assert out.report.invalid is None
    assert any("in-flight saver call" in o for o in out.report.observations)


def test_rows_no_in_flight_call_explains_are_invalid() -> None:
    artifacts = kill_while_parked(in_flight_lands=True)
    assert artifacts.snapshots is not None
    second = artifacts.snapshots.second
    stray = CheckpointRow(
        thread_id="T1",
        checkpoint_ns="",
        checkpoint_id="1f0afffe-0000-6000-8000-00000000fffe",
        parent_checkpoint_id=None,
        checkpoint={},
        metadata={},
        xmin="1",
        commit_ns=None,
    )
    changed = second.model_copy(update={"checkpoints": (*second.checkpoints, stray)})
    pair = artifacts.snapshots.model_copy(update={"second": changed})
    assert trigger(artifacts.model_copy(update={"snapshots": pair})) == 7


def test_a_kill_that_no_barrier_held_is_uncontrolled() -> None:
    """The same kill in a synthetic trial (not an artifact mutant) with the worker running."""
    killed = T.kill_mid_execute_trial()
    assert trigger(with_scenario(killed, evidence="synthetic")) == 16
    assert trigger(killed) is None  # an artifact mutant may plant it


def test_the_orchestrators_verdict_is_honored() -> None:
    harness = HarnessRecord(valid=False, invalid_reasons=("op resume-1 timed out while running",))
    out = run(T.w1_trial().model_copy(update={"harness": harness}))
    assert out.report.invalid is not None and out.report.invalid.trigger == 16
    assert "timed out while running" in out.report.invalid.reason


def test_provenance_must_match() -> None:
    base = T.w1_trial()
    assert (
        trigger(with_scenario(base, evidence="control")) == 1
    )  # control evidence, fixture provenance

    def elsewhere(events: list[Event]) -> list[Event]:
        return [events[0].model_copy(update={"trial": 7}), *events[1:]]

    assert trigger(M.edit_history(base, "worker-1", elsewhere)) == 1
    assert base.ledger is not None
    moved = base.ledger.model_copy(
        update={"rows": tuple(r.model_copy(update={"thread_id": "T9"}) for r in base.ledger.rows)}
    )
    assert trigger(base.model_copy(update={"ledger": moved})) == 1
    lost = T.lost_before_first_checkpoint_trial()
    assert trigger(T.as_run(lost, evidence="control").model_copy(update={"harness": None})) == 1
    assert trigger(with_scenario(T.as_run(lost, evidence="control"), threads=("T1",))) == 1


def test_missing_causal_evidence_is_invalid() -> None:
    base = T.w1_trial()

    def foreign_op(events: list[Event]) -> list[Event]:
        return [events[0].model_copy(update={"op_id": "resume-9"}), *events[1:]]

    assert trigger(M.edit_history(base, "worker-1", foreign_op)) == 3

    def stray_action(events: list[Event]) -> list[Event]:
        return [
            *events,
            events[0].model_copy(
                update={"kind": "fp.action", "op_id": None, "data": {"hit_seq": 0}}
            ),
        ]

    assert trigger(M.edit_history(base, "worker-1", stray_action)) == 3


def test_client_fail_is_observational() -> None:
    """A run whose client saw an error may still have committed everything; the oracle decides.
    Nothing is invalidated or judged from the failure itself."""

    def failed(events: list[Event]) -> list[Event]:
        return [
            e.model_copy(update={"kind": "client.fail", "status": "fail", "error": "lost result"})
            if e.kind == "client.ok" and e.op_id == "resume-1"
            else e
            for e in events
        ]

    got = outcomes(run(M.edit_history(T.w1_trial(), "orchestrator", failed)))
    # Its acknowledged checkpoints still count, and its effect is still reconciled with the state.
    assert got["I1b"] == ("PASS", "LG-DUR-SYNC")
    assert got["I7a"] == got["I7c"] == ("NO_ANOMALY_UNSPECIFIED", None)
    # The start op's ok still exercises I11; the failed op does not.
    assert got["I11"] == ("PASS", "LG-DUR-SYNC")
