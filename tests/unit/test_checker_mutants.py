"""Mutation tests: every artifact mutant flips exactly the outcome the specification names (3.4),
and every Layer A predicate can actually report an anomaly."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from pydantic import JsonValue

from crashcourt.checker.artifacts import Snapshot, TrialArtifacts, WriteRow
from crashcourt.checker.engine import prepare
from crashcourt.checker.invariants import PREDICATES
from crashcourt.checker.invariants.effects import i7b
from crashcourt.spec.events import Event
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_engine import outcomes, run

PREDICATE = dict(PREDICATES)


def status(artifacts: TrialArtifacts, invariant: str) -> str:
    context, _ = prepare(artifacts)
    return PREDICATE[invariant](context).status


# -- AM1-AM10 ---------------------------------------------------------------------------------


def test_am1_head_deleted() -> None:
    got = outcomes(run(M.am1_delete_head(T.w1_trial())))
    assert got["I1b"] == ("VIOLATION_CANDIDATE", "LG-DUR-SYNC")
    assert got["I6a"] == ("GAP", None)


def test_am2_middle_deleted() -> None:
    assert outcomes(run(M.am2_delete_middle(T.w1_trial())))["I5b"] == ("GAP", None)


def test_am3_second_child() -> None:
    assert outcomes(run(M.am3_second_child(T.w1_trial())))["I5a"] == ("GAP", None)


def test_am4_blob_deleted() -> None:
    assert outcomes(run(M.am4_delete_blob(T.w1_trial())))["I5c"] == ("GAP", None)


def test_am5_effect_deleted_is_the_i3_alarm() -> None:
    """Founder decision 4: the I3 anomaly makes the trial INVALID, never an outcome."""
    out = run(M.am5_delete_effect(T.w1_trial()))
    assert out.report.invalid is not None
    assert out.report.invalid.trigger == 15
    assert "I3 harness alarm" in out.report.invalid.reason
    assert out.result is not None
    assert {r.outcome for r in out.result.results} == {"INVALID"}


def test_am6_duplicate_without_fault() -> None:
    out = run(M.am6_duplicate_effect(T.w1_trial()))
    assert outcomes(out)["I4"] == ("GAP", None)
    assert any(i.startswith("I4:") for i in out.report.harness_investigations)


def test_am7_duplicate_after_kill_while_running() -> None:
    got = outcomes(run(T.kill_mid_execute_trial()))
    assert got["I4"] == ("EXPECTED", "LG-DE-RERUN")
    assert got["I7a"] == ("EXPECTED", "LG-DE-RERUN")


def test_am8_node_start_before_put_return() -> None:
    assert outcomes(run(M.am8_start_before_put_return(T.w1_trial())))["I1a"] == (
        "VIOLATION_CANDIDATE",
        "LG-DUR-SYNC",
    )


def test_am9_heal_dropped() -> None:
    out = run(M.am9_drop_heal(T.kill_mid_execute_trial()))
    assert out.report.invalid is not None and out.report.invalid.trigger == 9


def test_am10_lines_reordered() -> None:
    out = run(M.am10_reorder(T.w1_trial()))
    assert out.report.invalid is not None and out.report.invalid.trigger == 2


def test_mutants_also_flip_on_the_forced_fork_trial() -> None:
    fork = T.w5_fork_trial()
    assert outcomes(run(M.am2_delete_middle(fork)))["I5b"] == ("GAP", None)
    assert outcomes(run(M.am4_delete_blob(fork, "messages")))["I5c"] == ("GAP", None)


# -- every predicate can fail -----------------------------------------------------------------


def _set_head_value(channel: str, value: JsonValue) -> Callable[[Snapshot], Snapshot]:
    def edit(snapshot: Snapshot) -> Snapshot:
        head = max(snapshot.checkpoints, key=lambda r: r.checkpoint_id)
        values = dict(cast("dict[str, JsonValue]", head.checkpoint["channel_values"]))
        values[channel] = value
        changed = head.model_copy(
            update={"checkpoint": {**head.checkpoint, "channel_values": values}}
        )
        rows = tuple(changed if r is head else r for r in snapshot.checkpoints)
        return snapshot.model_copy(update={"checkpoints": rows})

    return edit


def _resume_writes(decision: str) -> Callable[[Snapshot], Snapshot]:
    from crashcourt.checker.decode import encode

    def edit(snapshot: Snapshot) -> Snapshot:
        rows: list[WriteRow] = []
        for w in snapshot.writes:
            if w.channel == "__resume__":
                value: object = (
                    [decision] if w.task_id != "00000000-0000-0000-0000-000000000000" else decision
                )
                type_, blob = encode(value)
                w = w.model_copy(update={"type": type_, "blob": blob})
            rows.append(w)
        return snapshot.model_copy(update={"writes": tuple(rows)})

    return edit


def _stale_read(artifacts: TrialArtifacts) -> TrialArtifacts:
    """worker-1's resume read returns the input checkpoint after it acknowledged later ones."""
    assert artifacts.snapshots is not None
    first = min(a.checkpoint_id for a in artifacts.snapshots.first.checkpoints)

    def edit(events: list[Event]) -> list[Event]:
        reads = [e for e in events if e.kind == "saver.get_tuple.return" and e.data.get("found")]
        target = reads[-1]
        return [e.model_copy(update={"checkpoint_id": first}) if e is target else e for e in events]

    return M.edit_history(artifacts, "worker-1", edit)


MUTANT_ANOMALIES: list[tuple[str, Callable[[], TrialArtifacts]]] = [
    ("I1a", lambda: M.am8_start_before_put_return(T.w1_trial())),
    ("I1b", lambda: M.am1_delete_head(T.w1_trial())),
    ("I2", lambda: T.kill_at_writes_trial("put_writes.after")),
    ("I4", lambda: M.am6_duplicate_effect(T.w1_trial())),
    ("I5a", lambda: M.am3_second_child(T.w1_trial())),
    ("I5b", lambda: M.am2_delete_middle(T.w1_trial())),
    ("I5c", lambda: M.am4_delete_blob(T.w1_trial())),
    ("I6a", lambda: M.am1_delete_head(T.w1_trial())),
    ("I6b", lambda: _stale_read(T.w1_trial())),
    ("I7a", lambda: M.am1_delete_head(T.w1_trial())),
    ("I7c", lambda: M.edit_snapshot(T.w1_trial(), _set_head_value("decision", "reject"))),
    ("I8", lambda: M.edit_snapshot(T.w1_trial(), _resume_writes("reject"))),
    ("I11", lambda: M.am1_delete_head(T.w1_trial())),
]


@pytest.mark.parametrize(
    ("invariant", "make"), MUTANT_ANOMALIES, ids=[m[0] for m in MUTANT_ANOMALIES]
)
def test_each_predicate_reports_its_anomaly(
    invariant: str, make: Callable[[], TrialArtifacts]
) -> None:
    mutated = make()
    assert status(mutated, invariant) == "ANOMALY"
    assert status(T.w1_trial(), invariant) in {"NO_ANOMALY", "NOT_EXERCISED"}


def test_i7b_phantom_reports_an_anomaly_but_never_an_outcome() -> None:
    """The phantom predicate fires on the same edit as the I3 alarm, so the trial is INVALID."""
    mutated = M.am5_delete_effect(T.w1_trial())
    context, _ = prepare(mutated)
    assert i7b(context).status == "ANOMALY"
    assert run(mutated).report.invalid is not None


def test_i6b_regression_is_gap_and_i7c_i8_mismatches_are_gap() -> None:
    assert outcomes(run(_stale_read(T.w1_trial())))["I6b"] == ("GAP", None)
    assert outcomes(run(M.edit_snapshot(T.w1_trial(), _set_head_value("decision", "reject"))))[
        "I7c"
    ] == ("GAP", None)
    out = run(M.edit_snapshot(T.w1_trial(), _resume_writes("reject")))
    assert outcomes(out)["I8"] == ("GAP", None)
    # One writer, mismatch: a harness investigation (specification 2.2, I8).
    assert any(i.startswith("I8:") for i in out.report.harness_investigations)
