"""The pre-freeze validation, run here on synthetic trials (real T-09 histories come later)."""

from __future__ import annotations

from crashcourt.checker.artifacts import LandmarkTable
from crashcourt.checker.landmarks import repository_tables
from tests.controls.prefreeze import validate
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_engine import REGISTRY, REGISTRY_SHA

(TABLE,) = repository_tables()


def _w1_table() -> LandmarkTable:
    """The repository table without its W5 landmark, so synthetic W1 trials can pass."""
    return TABLE.model_copy(
        update={"landmarks": tuple(s for s in TABLE.landmarks if s.id != "L-T1")}
    )


def test_fault_free_trials_validate() -> None:
    trials = [T.w1_trial() for _ in range(3)]
    report = validate(trials, _w1_table(), REGISTRY, REGISTRY_SHA, mutate=trials[0])
    assert report.passed, report.problems
    assert {k[0] for k in report.landmarks} == {"L-IN", "L-RES", "L-EXW", "L-EXC", "L-GET"}


def test_the_documented_misuse_duplicate_is_not_a_false_anomaly() -> None:
    report = validate([T.w1_trial(misuse=True)], _w1_table(), REGISTRY, REGISTRY_SHA)
    assert report.passed, report.problems


def test_an_ambiguous_landmark_fails_validation() -> None:
    """A rule that matches two calls in a run is not a landmark; validation reports it."""
    spec = next(s for s in TABLE.landmarks if s.id == "L-RES")
    loose = _w1_table().model_copy(
        update={
            "landmarks": tuple(
                spec.model_copy(update={"rules": spec.rules[:1]}) if s.id == "L-RES" else s
                for s in _w1_table().landmarks
            )
        }
    )
    report = validate([T.w1_trial()], loose, REGISTRY, REGISTRY_SHA)
    assert any("L-RES: 2 calls match" in p for p in report.problems)


def test_a_false_anomaly_or_a_dead_mutant_fails_validation() -> None:
    planted = M.am6_duplicate_effect(T.w1_trial())
    report = validate([planted], _w1_table(), REGISTRY, REGISTRY_SHA)
    assert any("false anomaly I4 GAP" in p for p in report.problems)
    invalid = T.w1_trial().model_copy(update={"ledger": None})
    report = validate([invalid], _w1_table(), REGISTRY, REGISTRY_SHA)
    assert any("above 2%" in p for p in report.problems)
