"""The checker mirrors formats T-06 and T-08 define; these pin that it still matches them."""

from __future__ import annotations

from typing import get_args

from crashcourt.checker.artifacts import ArmAction, ArmSpec, LedgerRow, Point
from crashcourt.failpoints.registry import ACTIONS, POINTS, SIGNALS, Arm
from crashcourt.sink.ledger import GENESIS, HASHED_FIELDS, row_hash
from tests.unit import _trials as T


def test_failpoints_match_t06() -> None:
    assert get_args(Point) == POINTS
    assert get_args(ArmAction) == ACTIONS
    arm_fields = {
        "point",
        "action",
        "worker",
        "op_id",
        "thread_id",
        "occurrence",
        "signal",
        "exit_code",
    }
    assert arm_fields == set(Arm.__dataclass_fields__)
    assert arm_fields | {"landmark"} == set(ArmSpec.model_fields)
    assert "SIGKILL" in SIGNALS and "SIGSTOP" in SIGNALS


def test_ledger_rows_match_t08() -> None:
    assert set(LedgerRow.model_fields) == {*HASHED_FIELDS, "prev_hash", "row_hash"}


def test_synthetic_ledgers_verify_under_t08s_chain() -> None:
    for artifacts in (T.w1_trial(), T.kill_mid_execute_trial(), T.w1_trial(misuse=True)):
        assert artifacts.ledger is not None and artifacts.ledger.rows
        prev = GENESIS
        for expected, row in enumerate(artifacts.ledger.rows, start=1):
            assert row.sink_seq == expected and row.prev_hash == prev
            assert row.row_hash == row_hash(prev, row.model_dump())
            prev = row.row_hash
