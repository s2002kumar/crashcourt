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


def test_contradiction_running_at_a_stop_and_the_c11_8039_shape() -> None:
    """CONTRADICTION (open). Section 2.0 defines a node as running at a stop when its node.start
    has no node.end before the stop; I4(a) and I7a's permission need the producing node running.
    LangGraph saves a task's writes only after its node returns (langgraph 1.2.12,
    pregel/_runner.py, commit(), whose success branch calls put_writes), so node.end precedes the
    task's put_writes; landmark L-EXW is defined as "after node.end execute". K1a / C11-8039 kills
    the worker at L-EXW's put_writes.before. By 2.0 execute is no longer running there, so (a)
    cannot hold and the duplicate falls to "none of the above": GAP, where K1a predicts EXPECTED
    (a) and C11-8039 must match its published outcome in 19 of 20 trials."""
    out = run(T.kill_at_writes_trial("put_writes.before"))
    got = outcomes(out)
    assert got["I4"] == ("GAP", None)
    assert got["I7a"] == ("GAP", None)
    # The same duplicate with the kill before node.end (AM7's shape) is EXPECTED under (a).
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
    """Founder decision 2 and the merged registry note on LG-PW: no verdict cites LG-PW for a
    crash. Until LG-PW-CRASH is registered, the crash case has no clause: I2 is
    NO_ANOMALY_UNSPECIFIED when the writes are reused and GAP when the task runs again, and a
    crash-time re-run is never PASS or VIOLATION_CANDIDATE."""
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


def test_reading_a_disclaimed_thread_has_no_durability_pass() -> None:
    """The PASS row needs the invariant's applicability guard; the GAP row makes any anomaly in a
    disclaimed scenario (more than one writer or resume on the thread) GAP. Read symmetrically: a
    disclaimed thread carries neither PASS nor a candidate."""
    from tests.unit.test_checker_faults import two_resumes

    got = outcomes(run(two_resumes()))
    assert got["I1b"] == got["I11"] == NAU
