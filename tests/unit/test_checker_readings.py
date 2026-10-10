"""What the checker does where the frozen methodology is read, or contradicts itself.

Each test pins the behavior of the specification as written (the red-team specification of
2026-10-09 with the founder's decisions in its section 11), so that settling a reading or a
contradiction before the freeze changes a test deliberately. The task record lists each one with
the passage it rests on. These are not predictions about LangGraph: every trial here is synthetic.
"""

from __future__ import annotations

from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_engine import NAU, outcomes, run
from tests.unit.test_checker_faults import trigger


def test_ruling_a_node_that_had_ended_was_not_running_at_the_stop() -> None:
    """Founder ruling of 2026-10-10. "Running when execution stopped" means node.start occurred and
    its matching node.end had not occurred before the stop; a node whose node.end occurred but whose
    task write was still in flight was not running, and pending-write semantics own that case.

    LangGraph saves a task's writes after its node returns (langgraph 1.2.12, pregel/_runner.py,
    commit(), success branch), so at the C11-8039 shape's kill (L-EXW's put_writes.before) execute
    had ended: I4(a) and I7a's re-run permission do not apply, no clause permits the re-run
    (LG-PW-CRASH describes it), and both are GAP, with the reason saying why. A kill before
    node.end (AM7's shape) is still EXPECTED under LG-DE-RERUN."""
    out = run(T.kill_at_writes_trial("put_writes.before"))
    got = outcomes(out)
    assert got["I4"] == ("GAP", None)
    assert got["I7a"] == ("GAP", None)
    assert out.result is not None
    for r in out.result.results:
        if r.invariant in {"I4", "I7a"}:
            assert "pending-write semantics own the re-run" in str(r.reason)
    assert not out.report.harness_investigations
    assert outcomes(run(T.kill_mid_execute_trial()))["I4"] == ("EXPECTED", "LG-DE-RERUN")


def test_open_question_the_c11_8764_gap_has_no_invariant() -> None:
    """OPEN. K4 / C11-8764 predicts "Outcome GAP (accepted run with no durable record)", but no
    invariant in section 2.2 observes it and every invariant's window is unexercised, so the trial
    has no outcomes at all. The checker records the run in checker.json as an observation and
    writes no result.json (schema v1 requires at least one outcome)."""
    out = run(T.lost_before_first_checkpoint_trial())
    assert out.result is None
    assert any(o.startswith("accepted run with no durable record") for o in out.report.observations)


def test_reading_a_crash_never_inherits_the_pending_writes_guarantee() -> None:
    """Founder decision 2: the crash case cites LG-PW-CRASH, which describes and carries no
    verdict. I2 is NO_ANOMALY_UNSPECIFIED when the writes are reused and GAP when the task runs
    again, and a crash-time re-run is never PASS or VIOLATION_CANDIDATE."""
    assert outcomes(run(T.kill_at_writes_trial("put_writes.after", rerun=False)))["I2"] == NAU
    assert outcomes(run(T.kill_at_writes_trial("put_writes.after")))["I2"] == ("GAP", None)
    # LG-PW's own case (a node raised in the super-step) still carries PASS and a candidate.
    assert outcomes(run(T.exception_case_trial(rerun=False)))["I2"] == ("PASS", "LG-PW-EXC")


def test_reading_a_missing_heal_is_trigger_9_not_7() -> None:
    """Trigger 7 (a snapshot taken before a fault's heal) and trigger 9 (an inject without its
    heal) both describe a missing heal; AM9 names 9. Trigger 7 is read as a heal that came after
    the snapshot."""
    assert trigger(M.am9_drop_heal(T.kill_mid_execute_trial())) == 9


def test_reading_multi_writer_head_is_always_a_gap_observation() -> None:
    """I6a: "Multi-writer: record which writer's checkpoint is the head and why (larger id), as a
    GAP observation." Read as GAP whenever the thread had concurrent writers."""
    out = run(T.w5_fork_trial())
    assert outcomes(out)["I6a"] == ("GAP", None)


def test_ruling_a_disclaimed_thread_keeps_its_durability_verdicts() -> None:
    """Founder ruling D1 (2026-10-10): LGP-DT's disclaimer and harness-originated events apply to
    the invariants about what a second run does (I2, I4-I8), never to I1a, I1b, or I11. A thread
    with a second resume keeps durability PASS; I4 there has no PASS."""
    from tests.unit.test_checker_faults import two_resumes

    got = outcomes(run(two_resumes()))
    assert got["I1b"] == got["I11"] == ("PASS", "LG-DUR-SYNC")
    assert got["I4"] == NAU


def test_reading_a_retry_after_client_fail_is_a_second_resume() -> None:
    """I4(c) counts "two `client.invoke` with resume commands after one interrupt"; a client's
    retry after client.fail is one, so the retry cells' threads are disclaimed like F07's for I2
    and I4-I8. Durability is still judged (founder ruling D1)."""
    t = T.TrialBuilder()
    start = t.op("start-1", "start")
    first = t.op("resume-1", "resume", resume="approve")
    retry = t.op("resume-2", "resume", resume="approve")
    t.invoke(start)
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1")
    t.ok("start-1", "worker-1")
    t.invoke(first)
    t.dispatch("resume-1", "worker-1")
    t.w1_resume("worker-1", "resume-1", "T1", "approve")
    t.fail("resume-1", "worker-1", "lost result")
    t.invoke(retry)
    t.dispatch("resume-2", "worker-1")
    t.get_tuple(t.workers["worker-1"], "resume-2", "T1")
    t.ok("resume-2", "worker-1")
    t.exit("worker-1")
    out = run(t.build())
    got = outcomes(out)
    assert got["I1a"] == got["I1b"] == got["I11"] == ("PASS", "LG-DUR-SYNC")
    assert got["I4"] == NAU
    i4 = next(d for d in out.report.details if d.invariant == "I4")
    assert any(w.name == "disclaimed" and w.held for w in i4.witnesses)
