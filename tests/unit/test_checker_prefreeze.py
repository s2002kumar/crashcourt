"""The pre-freeze validation, run here on synthetic trials (real T-09 histories come later)."""

from __future__ import annotations

from crashcourt.checker.artifacts import ContextRule, LandmarkSpec, LandmarkTable
from crashcourt.checker.landmarks import repository_tables
from tests.controls.prefreeze import validate
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_engine import REGISTRY, REGISTRY_SHA

(TABLE,) = repository_tables()


def _w1_table() -> LandmarkTable:
    """The draft table without its W5 landmark, and L-RES narrowed to the first call, as an audit
    might settle it; only so that synthetic W1 trials can pass."""
    keep: list[LandmarkSpec] = []
    for spec in TABLE.landmarks:
        if spec.id == "L-T1":
            continue
        if spec.id == "L-RES":
            spec = spec.model_copy(update={"rules": (*spec.rules, ContextRule(rule="first_call"))})
        keep.append(spec)
    return TABLE.model_copy(update={"landmarks": tuple(keep)})


def test_fault_free_trials_validate() -> None:
    trials = [T.w1_trial() for _ in range(3)]
    report = validate(trials, _w1_table(), REGISTRY, REGISTRY_SHA, mutate=trials[0])
    assert report.passed, report.problems
    assert {k[0] for k in report.landmarks} == {"L-IN", "L-RES", "L-EXW", "L-EXC"}


def test_the_documented_misuse_duplicate_is_not_a_false_anomaly() -> None:
    report = validate([T.w1_trial(misuse=True)], _w1_table(), REGISTRY, REGISTRY_SHA)
    assert report.passed, report.problems


def test_the_draft_table_as_written_fails_validation() -> None:
    """4.2's L-RES matches two calls; validation reports it rather than choosing one."""
    report = validate([T.w1_trial()], TABLE, REGISTRY, REGISTRY_SHA)
    assert any("L-RES: 2 calls match" in p for p in report.problems)


def test_a_false_anomaly_or_a_dead_mutant_fails_validation() -> None:
    planted = M.am6_duplicate_effect(T.w1_trial())
    report = validate([planted], _w1_table(), REGISTRY, REGISTRY_SHA)
    assert any("false anomaly I4 GAP" in p for p in report.problems)
    invalid = T.w1_trial().model_copy(update={"ledger": None})
    report = validate([invalid], _w1_table(), REGISTRY, REGISTRY_SHA)
    assert any("above 2%" in p for p in report.problems)
