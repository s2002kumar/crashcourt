"""The checker end to end on synthetic trials: outcomes, omission, determinism, INVALID."""

from __future__ import annotations

import random

import pytest

from crashcourt.checker import CheckerOutput, check_trial
from crashcourt.checker.artifacts import TrialArtifacts
from crashcourt.checker.invariants import INVARIANTS
from crashcourt.spec.records import ResultRecord
from tests.unit import _trials as T

REGISTRY, REGISTRY_SHA = T.registry()


def run(artifacts: TrialArtifacts) -> CheckerOutput:
    return check_trial(artifacts, REGISTRY, registry_sha256=REGISTRY_SHA)


def outcomes(out: CheckerOutput) -> dict[str, tuple[str, str | None]]:
    assert out.report.invalid is None, out.report.invalid
    assert out.result is not None
    return {r.invariant: (r.outcome, r.clause) for r in out.result.results}


def omitted(out: CheckerOutput) -> set[str]:
    return {n.invariant for n in out.report.not_exercised}


NAU = ("NO_ANOMALY_UNSPECIFIED", None)
SYNC_PASS = ("PASS", "LG-DUR-SYNC")


def test_fault_free_approve_under_sync() -> None:
    out = run(T.w1_trial())
    got = outcomes(out)
    assert got["I1a"] == got["I1b"] == got["I11"] == SYNC_PASS
    # No duplicates and no crash: LG-PW's guarantee (2.3: "I4 PASS (no duplicates)").
    assert got["I4"] == ("PASS", "LG-PW")
    for name in ("I5a", "I5b", "I5c", "I6a", "I6b", "I7a", "I7b", "I7c", "I8"):
        assert got[name] == NAU, name
    assert omitted(out) == {"I2"}
    assert out.report.harness_investigations == ()


def test_fault_free_reject_omits_the_effect_invariants() -> None:
    out = run(T.w1_trial("reject"))
    assert omitted(out) == {"I2", "I4", "I7a", "I7b", "I7c", "I8"}
    assert outcomes(out)["I1b"] == SYNC_PASS


def test_misuse_duplicate_is_expected_under_the_interrupt_clause() -> None:
    """Control 3.3, run 1: the precheck posted before interrupt() repeats on resume."""
    got = outcomes(run(T.w1_trial(misuse=True)))
    assert got["I4"] == ("EXPECTED", "LG-INT-RERUN")
    assert got["I7c"] == NAU


def test_misuse_with_reject_still_expected_and_approval_holds() -> None:
    """Control 3.3, run 3: two precheck rows, zero charge rows, I7c holds."""
    got = outcomes(run(T.w1_trial("reject", misuse=True)))
    assert got["I4"] == ("EXPECTED", "LG-INT-RERUN")
    assert got["I7c"] == NAU


def test_async_carries_no_durability_verdict() -> None:
    out = run(T.w1_trial(durability="async"))
    got = outcomes(out)
    assert "I1a" in omitted(out)
    assert got["I1b"] == got["I11"] == NAU


def test_forced_fork_is_detected_and_the_head_recorded() -> None:
    """Control C2's shape: I5a GAP (fork) and I6a GAP naming the larger id and its writer."""
    out = run(T.w5_fork_trial())
    got = outcomes(out)
    assert got["I5a"] == ("GAP", None)
    assert got["I6a"] == ("GAP", None)
    assert out.result is not None
    i6a = next(r for r in out.result.results if r.invariant == "I6a")
    assert "largest id" in str(i6a.reason) and "worker-2" in str(i6a.reason)
    # Two writers: no durability PASS, since LGP-DT disclaims the scenario.
    assert got["I1b"] == NAU


def test_nothing_exercised_writes_no_result_and_says_why() -> None:
    """The C11-8764 shape: no rows, client.info. No invariant is exercised, so there is no
    result.json (schema v1 needs one outcome), and checker.json records the accepted run."""
    out = run(T.lost_before_first_checkpoint_trial())
    assert out.result is None and out.result_json() is None
    assert omitted(out) == set(INVARIANTS)
    assert any("accepted run with no durable record" in o for o in out.report.observations)


def test_unexercised_invariants_are_omitted_not_passed() -> None:
    for artifacts in (
        T.w1_trial("reject"),
        T.w5_fork_trial(),
        T.lost_before_first_checkpoint_trial(),
    ):
        out = run(artifacts)
        listed = {r.invariant for r in out.result.results} if out.result else set()
        assert listed.isdisjoint(omitted(out))
        assert listed | omitted(out) == set(INVARIANTS)


def test_output_is_byte_identical_across_runs_and_history_order() -> None:
    for artifacts in (T.w1_trial(), T.w5_fork_trial(), T.kill_mid_execute_trial()):
        first = run(artifacts)
        shuffled = list(artifacts.histories)
        random.Random(7).shuffle(shuffled)
        second = run(artifacts.model_copy(update={"histories": tuple(shuffled)}))
        assert first.result_json() == second.result_json()
        assert first.report_json() == second.report_json()


def test_monotonic_clocks_do_not_change_the_verdicts() -> None:
    """mono_ns is never a verdict input: scrambling it changes no byte of the output."""
    artifacts = T.kill_mid_execute_trial()
    rng = random.Random(3)
    scrambled = tuple(
        h.model_copy(
            update={
                "events": tuple(
                    e.model_copy(update={"mono_ns": rng.randrange(10**9)}) for e in h.events
                )
            }
        )
        for h in artifacts.histories
    )
    a, b = run(artifacts), run(artifacts.model_copy(update={"histories": scrambled}))
    assert a.result_json() == b.result_json()


def test_results_validate_as_schema_v1_records() -> None:
    for artifacts in (T.w1_trial(), T.w5_fork_trial(), T.kill_at_writes_trial("put_writes.after")):
        text = run(artifacts).result_json()
        assert text is not None
        ResultRecord.model_validate_json(text)


def test_invalid_applies_to_every_invariant_and_names_the_trigger() -> None:
    artifacts = T.w1_trial()
    broken = artifacts.model_copy(update={"ledger": None})
    out = run(broken)
    assert out.report.invalid is not None and out.report.invalid.trigger == 1
    assert out.result is not None
    assert [r.invariant for r in out.result.results] == list(INVARIANTS)
    assert {r.outcome for r in out.result.results} == {"INVALID"}
    assert all(str(r.reason).startswith("trigger 1 (incomplete trial)") for r in out.result.results)


def test_a_checker_that_raises_is_invalid_not_a_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    import crashcourt.checker.engine as engine

    def boom(context: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(engine, "PREDICATES", (("I1a", boom),))
    out = run(T.w1_trial())
    assert out.report.invalid is not None and out.report.invalid.trigger == 14
    assert "RuntimeError: bug" in out.report.invalid.reason


def test_report_is_marked_pre_freeze() -> None:
    report = run(T.w1_trial()).report
    assert report.freeze_state == "PRE-FREEZE"
    assert report.authorized_for_new_evidence is False
    assert report.frozen_sha256 is None
    assert report.registry_sha256 == REGISTRY_SHA
