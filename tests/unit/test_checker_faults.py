"""Fault-fired verification (3.6) and landmark verification (4.2): a fault that did not fire, or
fired elsewhere than declared, is INVALID and never reads as PASS."""

from __future__ import annotations

from typing import Any

import pytest

from crashcourt.checker.artifacts import (
    ArmSpec,
    ContextRule,
    FaultKind,
    FaultSpec,
    LandmarkSpec,
    LandmarkTable,
    Snapshot,
    TrialArtifacts,
    WorkerReport,
)
from crashcourt.checker.status import NotAuthorized
from crashcourt.spec.events import Event
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_engine import outcomes, run


def trigger(artifacts: TrialArtifacts) -> int | None:
    out = run(artifacts)
    return out.report.invalid.trigger if out.report.invalid is not None else None


def with_scenario(artifacts: TrialArtifacts, **changes: Any) -> TrialArtifacts:
    return artifacts.model_copy(update={"scenario": artifacts.scenario.model_copy(update=changes)})


def edit_events(artifacts: TrialArtifacts, proc: str, change: Any) -> TrialArtifacts:
    return M.edit_history(artifacts, proc, lambda es: [change(e) for e in es])


# -- arms ---------------------------------------------------------------------------------------


def test_fired_faults_pass_verification() -> None:
    for artifacts in (
        T.kill_at_writes_trial("put_writes.before"),
        T.kill_mid_execute_trial(),
        T.w5_fork_trial(),
        T.stop_and_redeliver_fork_trial(),
        T.lost_before_first_checkpoint_trial(),
    ):
        assert trigger(artifacts) is None


def test_an_arm_that_never_hit_is_invalid() -> None:
    artifacts = T.kill_at_writes_trial("put_writes.before")
    arm = artifacts.scenario.arms[0].model_copy(update={"occurrence": 2})
    assert trigger(with_scenario(artifacts, arms=(arm,))) == 10


def test_a_failed_hit_is_invalid() -> None:
    artifacts = edit_events(
        T.kill_at_writes_trial("put_writes.before"),
        "worker-1",
        lambda e: (
            e.model_copy(update={"status": "fail", "error": "FailpointError"})
            if e.kind == "fp.hit"
            else e
        ),
    )
    assert trigger(artifacts) == 10


def test_an_arm_that_acted_otherwise_is_invalid() -> None:
    artifacts = T.kill_at_writes_trial("put_writes.before")
    arm = artifacts.scenario.arms[0].model_copy(update={"action": "pause", "signal": None})
    assert trigger(with_scenario(artifacts, arms=(arm,))) == 10


def test_failed_arms_at_shutdown_are_invalid() -> None:
    artifacts = T.w1_trial().model_copy(
        update={
            "worker_reports": (WorkerReport(proc="worker-1", failpoints_failed=("put.before",)),)
        }
    )
    assert trigger(artifacts) == 10


# -- symptoms -----------------------------------------------------------------------------------


def test_sigkill_needs_the_signal_on_exit_and_silence_after() -> None:
    base = T.kill_at_writes_trial("put_writes.before")
    no_signal = edit_events(
        base,
        "orchestrator",
        lambda e: (
            e.model_copy(update={"data": {"proc": e.data["proc"], "returncode": 1}})
            if e.kind == "worker.exit" and e.data.get("proc") == "worker-1"
            else e
        ),
    )
    assert trigger(no_signal) == 12

    def talk_after(events: list[Event]) -> list[Event]:
        action = next(e for e in events if e.kind == "fp.action")
        return [
            *events,
            action.model_copy(update={"kind": "saver.get_tuple.call", "fp": None, "data": {}}),
        ]

    assert trigger(M.edit_history(base, "worker-1", talk_after)) == 12


def test_exit_needs_its_status() -> None:
    def as_exit(e: Event) -> Event:
        if e.kind in {"fp.hit", "fp.action"}:
            return e.model_copy(update={"data": {**e.data, "action": "exit"}})
        return e

    base = edit_events(T.lost_before_first_checkpoint_trial(), "worker-1", as_exit)
    base = edit_events(
        base,
        "orchestrator",
        lambda e: (
            e.model_copy(update={"data": {"proc": "worker-1", "returncode": 3}})
            if e.kind == "worker.exit"
            else e
        ),
    )
    arm = ArmSpec(
        point="put.before", action="exit", exit_code=3, worker="worker-1", op_id="start-1"
    )
    ok = with_scenario(base, arms=(arm,), faults=(FaultSpec(kind="exit", arm=0, exit_code=3),))
    assert trigger(ok) is None
    wrong = with_scenario(base, arms=(arm,), faults=(FaultSpec(kind="exit", arm=0, exit_code=4),))
    assert trigger(wrong) == 12


def test_pause_needs_its_release() -> None:
    fork = T.w5_fork_trial()
    released = [e for h in fork.histories for e in h.events if e.kind == "fp.release"]
    assert released

    def drop_first_release(events: list[Event]) -> list[Event]:
        first = next(e for e in events if e.kind == "fp.release")
        return [e for e in events if e is not first]

    assert trigger(M.edit_history(fork, "orchestrator", drop_first_release)) == 12


def test_sigstop_needs_the_worker_to_go_on() -> None:
    base = T.stop_and_redeliver_fork_trial()

    def stop_for_good(events: list[Event]) -> list[Event]:
        action = next(e for e in events if e.kind == "fp.action")
        return events[: action.seq + 1]

    assert trigger(M.edit_history(base, "worker-1", stop_for_good)) == 12


def test_redelivery_needs_the_named_worker() -> None:
    base = T.kill_mid_execute_trial()
    faults = (
        base.scenario.faults[0],
        FaultSpec(kind="redelivery", op_ids=("resume-1",), worker="worker-9"),
    )
    assert trigger(with_scenario(base, faults=faults)) == 12


def two_resumes(order: tuple[str, str] = ("resume-1", "resume-2")) -> TrialArtifacts:
    """F07's shape: resume-1 completes, then resume-2 with the same value on the same thread."""
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
    t.w1_resume("worker-1", "resume-1", "T1", "approve")
    t.ok("resume-1", "worker-1")
    t.invoke(second)
    t.dispatch("resume-2", "worker-1")
    t.get_tuple(t.workers["worker-1"], "resume-2", "T1")
    t.ok("resume-2", "worker-1")
    t.exit("worker-1")
    t.faults.append(FaultSpec(kind="second-resume", op_ids=order))
    return t.build()


def test_second_resume_needs_both_in_the_declared_order() -> None:
    assert trigger(two_resumes()) is None
    assert trigger(two_resumes(("resume-2", "resume-1"))) == 12
    # Two resumes on one thread: LGP-DT disclaims what a second run does, not durability (D1).
    assert outcomes(run(two_resumes()))["I1b"] == ("PASS", "LG-DUR-SYNC")


TOXIC = {
    "lost-ack": ("drop_responses", "timeout", "downstream"),
    "reset": ("reset_connections", "reset_peer", "upstream"),
    "delayed-ack": ("hold_responses", "latency", "downstream"),
}


def network_fault_trial(
    kind: FaultKind,
    *,
    error: str,
    commit: bool,
    returns: bool = False,
    toxic: tuple[str, str, str] | None = None,
    proxy: str = "pg-worker-1",
    inject_after_release: bool = False,
) -> TrialArtifacts:
    """A network fault on execute's put_writes, paused at put_writes.before: the toxic goes on,
    the call is released, and then raises (lost-ack, reset) or returns (delayed-ack). On a raise
    the client sees a failure and retries as resume-2, which runs execute again."""
    t = T.TrialBuilder()
    start = t.op("start-1", "start")
    resume = t.op("resume-1", "resume", resume="approve")
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1")
    t.ok("start-1", "worker-1")
    t.invoke(resume)
    t.dispatch("resume-1", "worker-1")
    t.w1_approve("worker-1", "resume-1", "T1", "approve")
    w = t.workers["worker-1"]
    c2 = str(t.graphs["T1"].head)
    task = t.task("T1", "execute")
    t.node(w, "resume-1", "execute", task, end=False)
    attempt = t.effect(w, "resume-1", "T1", "execute", "charge:T1", decision="approve")
    t.node_end(w, "resume-1", "execute", task)
    fields = {"thread_id": "T1", "checkpoint_ns": "", "checkpoint_id": c2, "task_id": task}
    w.emit(
        "saver.put_writes.call",
        op_id="resume-1",
        **fields,
        data={"channels": ["receipt"], "task_path": ""},
    )
    hit = t.fp(w, "put_writes.before", "resume-1", "pause", **fields)
    fault, toxic_type, stream = toxic or TOXIC[kind]
    if not inject_after_release:
        t.inject("net-1", fault=fault, proxy=proxy, toxic=toxic_type, stream=stream)
    t.orch.emit("fp.release", data={"proc": "worker-1", "hit_seq": hit.seq})
    if inject_after_release:
        t.inject("net-1", fault=fault, proxy=proxy, toxic=toxic_type, stream=stream)
    t.act(w, hit)
    receipt = ["charge:T1", attempt]
    if commit:
        t.commit_writes("T1", c2, task, [("receipt", receipt)])
    if returns:
        w.emit("saver.put_writes.return", op_id="resume-1", **fields, status="ok", data={})
        t.heal("net-1")
        t.w1_finish("worker-1", "resume-1", "T1", [receipt], receipt=receipt)
        t.ok("resume-1", "worker-1")
    else:
        w.emit(
            "saver.put_writes.raise",
            op_id="resume-1",
            **fields,
            status="fail",
            error=f"OperationalError: {error}",
            data={"stage": "inner"},
        )
        t.heal("net-1")
        t.fail("resume-1", "worker-1", error)
        t.invoke(t.op("resume-2", "resume", resume="approve"))
        t.dispatch("resume-2", "worker-1")
        t.get_tuple(w, "resume-2", "T1")
        _, again = t.w1_execute("worker-1", "resume-2", "T1", "approve")
        entry = ["charge:T1", again]
        t.w1_finish("worker-1", "resume-2", "T1", [entry], receipt=entry)
        t.ok("resume-2", "worker-1")
    t.exit("worker-1")
    t.arms.append(
        ArmSpec(
            point="put_writes.before",
            action="pause",
            worker="worker-1",
            op_id="resume-1",
            occurrence=int(str(hit.data["occurrence"])),
        )
    )
    t.faults.extend([FaultSpec(kind="pause", arm=0), FaultSpec(kind=kind, arm=0, fault_id="net-1")])
    return t.build(proxies={"pg-worker-1": "worker-1", "pg-worker-2": "worker-2"})


LOST = "server closed the connection unexpectedly"
RESET = "consuming input failed: server closed the connection unexpectedly"


def test_lost_ack_needs_the_error_and_the_commit_inside_the_window() -> None:
    lost = network_fault_trial("lost-ack", error=LOST, commit=True)
    assert trigger(lost) is None
    assert trigger(network_fault_trial("lost-ack", error="something else", commit=True)) == 12
    assert trigger(network_fault_trial("lost-ack", error=LOST, commit=False)) == 12

    def late(snapshot: Snapshot) -> Snapshot:
        rows = tuple(
            w.model_copy(update={"commit_ns": 1}) if w.channel == "receipt" else w
            for w in snapshot.writes
        )
        return snapshot.model_copy(update={"writes": rows})

    assert trigger(M.edit_snapshot(lost, late)) == 12


def test_a_verified_lost_ack_is_i2s_commit_witness() -> None:
    """The commit a verified lost acknowledgment proves is route (c) of I2's committed-writes
    witness: the retry ran execute again, which is the crash family (no guaranteeing clause)."""
    got = outcomes(run(network_fault_trial("lost-ack", error=LOST, commit=True)))
    assert got["I2"] == ("GAP", None)
    # Two resumes on one thread: the duplicate is cause (c), GAP.
    assert got["I4"] == ("GAP", None)


def test_reset_needs_the_row_absent() -> None:
    assert trigger(network_fault_trial("reset", error=RESET, commit=False)) is None
    assert trigger(network_fault_trial("reset", error=RESET, commit=True)) == 12
    # "The raise as above": any other error is not the fault's symptom.
    assert trigger(network_fault_trial("reset", error="statement timeout", commit=False)) == 12


def test_a_network_fault_must_have_touched_the_held_call() -> None:
    """Section 8 item 2: a fault that never fired must not read as PASS. The toxic must be the
    row's (type and stream), on the targeted worker's proxy, installed before the held call was
    released."""
    assert trigger(network_fault_trial("lost-ack", error=LOST, commit=True)) is None
    wrong_type = ("hold_responses", "latency", "downstream")
    assert trigger(network_fault_trial("lost-ack", error=LOST, commit=True, toxic=wrong_type)) == 12
    wrong_stream = ("reset_connections", "reset_peer", "downstream")
    assert (
        trigger(network_fault_trial("reset", error=RESET, commit=False, toxic=wrong_stream)) == 12
    )
    other_worker = network_fault_trial("lost-ack", error=LOST, commit=True, proxy="pg-worker-2")
    assert trigger(other_worker) == 12
    unknown_proxy = network_fault_trial("lost-ack", error=LOST, commit=True, proxy="pg-x")
    assert trigger(unknown_proxy) == 12
    late = network_fault_trial(
        "delayed-ack", error="", commit=True, returns=True, inject_after_release=True
    )
    assert trigger(late) == 12


def test_delayed_ack_needs_the_return_and_the_row() -> None:
    ok = network_fault_trial("delayed-ack", error="", commit=True, returns=True)
    out = run(ok)
    assert out.report.invalid is None
    assert any("delayed-ack" in n for n in out.report.fault_notes)
    assert trigger(network_fault_trial("delayed-ack", error=LOST, commit=True)) == 12


def backend_fault_trial(
    kind: str, heal: dict[str, Any], error: str, postmaster_ns: int = 0
) -> TrialArtifacts:
    """A database fault while the resume is held at get_tuple.before; once released, its first
    statement fails."""
    specs: dict[str, FaultKind] = {
        "terminate_backends": "terminate-backend",
        "restart_database": "db-restart",
    }
    spec = specs[kind]
    t = T.TrialBuilder()
    start = t.op("start-1", "start")
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1")
    t.ok("start-1", "worker-1")
    w = t.workers["worker-1"]
    resume = t.op("resume-1", "resume", resume="approve")
    t.invoke(resume)
    t.dispatch("resume-1", "worker-1")
    w.emit("saver.get_tuple.call", op_id="resume-1", thread_id="T1", checkpoint_ns="")
    hit = t.fp(w, "get_tuple.before", "resume-1", "pause", thread_id="T1", checkpoint_ns="")
    t.inject("db-1", fault=kind)
    t.heal("db-1", **heal)
    t.orch.emit("fp.release", data={"proc": "worker-1", "hit_seq": hit.seq})
    t.act(w, hit)
    w.emit(
        "saver.get_tuple.raise",
        op_id="resume-1",
        thread_id="T1",
        checkpoint_ns="",
        status="fail",
        error=f"OperationalError: {error}",
        data={"stage": "inner"},
    )
    t.fail("resume-1", "worker-1", error)
    t.exit("worker-1")
    t.arms.append(
        ArmSpec(point="get_tuple.before", action="pause", worker="worker-1", op_id="resume-1")
    )
    t.faults.extend(
        [FaultSpec(kind="pause", arm=0), FaultSpec(kind=spec, fault_id="db-1", worker="worker-1")]
    )
    artifacts = t.build()
    return M.edit_snapshot(
        artifacts,
        lambda s: s.model_copy(
            update={"meta": s.meta.model_copy(update={"postmaster_start_ns": postmaster_ns})}
        ),
    )


def test_terminate_backend_needs_a_terminated_backend_and_57p01() -> None:
    err = "terminating connection due to administrator command (57P01)"
    assert trigger(backend_fault_trial("terminate_backends", {"terminated": 1}, err)) is None
    assert trigger(backend_fault_trial("terminate_backends", {"terminated": 0}, err)) == 12
    assert trigger(backend_fault_trial("terminate_backends", {"terminated": 1}, "timeout")) == 12


def test_db_restart_needs_durations_errors_and_a_new_postmaster() -> None:
    err = "terminating connection due to administrator command"
    later = 2_000_000_000_000_000_000
    good = {"restart_s": 1.5, "ready_s": 0.4}
    assert trigger(backend_fault_trial("restart_database", good, err, later)) is None
    assert trigger(backend_fault_trial("restart_database", {}, err, later)) == 12
    assert trigger(backend_fault_trial("restart_database", good, err, 0)) == 12


# -- landmarks ----------------------------------------------------------------------------------


def l_in(status: str = "audited") -> LandmarkTable:
    """A test table whose first landmark uses the input-checkpoint rules of 4.2's verification
    column. Marked audited only to exercise run-evidence verification; the repository's table is
    derived from real T-09 histories."""
    occurrence = 1 if status == "audited" else None
    return LandmarkTable(
        name="DEMO-landmarks",
        status=status,  # pyright: ignore[reportArgumentType]
        landmarks=(
            LandmarkSpec(
                id="L-IN",
                method="put",
                rules=(
                    ContextRule(rule="call_data_equals", key="source", value="input"),
                    ContextRule(rule="no_kind_before", kind="node.start"),
                ),
                occurrence=occurrence,
            ),
            LandmarkSpec(
                id="L-WRONG",
                method="put",
                rules=(ContextRule(rule="after_node_end", node="plan"),),
                occurrence=occurrence,
            ),
        ),
    )


def replication(landmark: str | None, table: LandmarkTable | None = None) -> TrialArtifacts:
    """The C11-8764 shape as a harness run replicating langgraph#8764."""
    fixture = T.lost_before_first_checkpoint_trial(landmarks=table or l_in(), landmark=landmark)
    return T.as_run(fixture, evidence="replication", replication_of="langgraph#8764")


def test_an_unlandmarked_arm_is_invalid_outside_synthetic_trials() -> None:
    assert trigger(replication(None)) == 11


def test_a_hit_at_its_landmark_passes_and_elsewhere_is_invalid() -> None:
    assert trigger(replication("L-IN")) is None
    assert trigger(replication("L-WRONG")) == 11
    assert trigger(replication("L-NONE")) == 11


def test_a_landmark_of_another_method_is_invalid() -> None:
    table = LandmarkTable(
        name="DEMO-landmarks",
        status="audited",
        landmarks=(
            LandmarkSpec(
                id="L-IN", method="put_writes", rules=l_in().landmarks[0].rules, occurrence=1
            ),
        ),
    )
    assert trigger(replication("L-IN", table)) == 11


def test_an_arm_must_name_the_audited_occurrence() -> None:
    """An audited table records each landmark's occurrence; an arm naming another is INVALID even
    if its hit happens to satisfy the rules."""
    table = l_in()
    moved = table.model_copy(
        update={"landmarks": (table.landmarks[0].model_copy(update={"occurrence": 2}),)}
    )
    assert trigger(replication("L-IN", moved)) == 11


def test_a_table_states_occurrences_exactly_when_audited() -> None:
    spec = l_in().landmarks[0]
    with pytest.raises(ValueError, match="draft"):
        LandmarkTable(name="DEMO-landmarks", status="draft", landmarks=(spec,))
    with pytest.raises(ValueError, match="audited"):
        LandmarkTable(
            name="DEMO-landmarks",
            status="audited",
            landmarks=(spec.model_copy(update={"occurrence": None}),),
        )


def test_a_draft_table_verifies_nothing_outside_synthetic_trials() -> None:
    assert trigger(replication("L-IN", l_in("draft"))) == 11
    synthetic = T.lost_before_first_checkpoint_trial(landmarks=l_in("draft"), landmark="L-IN")
    assert trigger(synthetic) is None


EXW_RULES = {
    "call_task_of_node": ContextRule(rule="call_task_of_node", node="execute"),
    "after_node_end": ContextRule(rule="after_node_end", node="execute"),
    "before_node_start": ContextRule(rule="before_node_start", node="record"),
    "call_channels_include": ContextRule(rule="call_channels_include", channel="receipt"),
    "last_call": ContextRule(rule="last_call"),
    "call_data_equals": ContextRule(rule="call_data_equals", key="task_path", value=""),
}
EXW_FAILING = {
    "call_task_of_node": ContextRule(rule="call_task_of_node", node="approval"),
    "after_node_end": ContextRule(rule="after_node_end", node="record"),
    "before_node_start": ContextRule(rule="before_node_start", node="execute"),
    "call_channels_include": ContextRule(rule="call_channels_include", channel="__interrupt__"),
    "no_kind_before": ContextRule(rule="no_kind_before", kind="node.start"),
    "call_data_equals": ContextRule(rule="call_data_equals", key="task_path", value="x"),
}


def exw(rule: ContextRule) -> TrialArtifacts:
    base = T.kill_at_writes_trial("put_writes.before")
    table = LandmarkTable(
        name="DEMO-landmarks",
        status="draft",
        landmarks=(LandmarkSpec(id="L-X", method="put_writes", rules=(rule,)),),
    )
    arm = base.scenario.arms[0].model_copy(update={"landmark": "L-X"})
    return with_scenario(base, arms=(arm,), landmarks=table)


@pytest.mark.parametrize("name", sorted(EXW_RULES))
def test_each_context_rule_can_hold(name: str) -> None:
    assert trigger(exw(EXW_RULES[name])) is None


@pytest.mark.parametrize("name", sorted(EXW_FAILING))
def test_each_context_rule_can_fail(name: str) -> None:
    assert trigger(exw(EXW_FAILING[name])) == 11


def test_new_evidence_is_refused_before_the_freeze() -> None:
    with pytest.raises(NotAuthorized):
        run(with_scenario(T.w1_trial(), evidence="new-evidence"))
    with pytest.raises(NotAuthorized):
        run(with_scenario(T.w1_trial(), matrix_cell="N4"))
    with pytest.raises(NotAuthorized):
        run(with_scenario(T.w1_trial(), evidence="replication", replication_of="langgraph#9185"))
