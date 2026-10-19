"""Observations matched against a trial's records: the ledger rows' statuses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crashcourt.experiment.observe import mismatch
from crashcourt.experiment.preregistration import LedgerRows
from crashcourt.experiment.verify import TrialFiles


def trial(tmp_path: Path, rows: list[dict[str, Any]]) -> TrialFiles:
    (tmp_path / "ledger.json").write_text(
        json.dumps({"rows": rows, "verify_ok": True}), encoding="utf-8"
    )
    return TrialFiles(1, tmp_path, {}, None)


def row(seq: int, attempt: int, status: str) -> dict[str, Any]:
    return {"sink_seq": seq, "effect_key": "charge:t1", "attempt": attempt, "status": status}


def test_statuses_are_matched_in_the_sinks_order_when_predicted(tmp_path: Path) -> None:
    t = trial(tmp_path, [row(2, 2, "duplicate"), row(1, 1, "applied")])
    once = LedgerRows(
        source="ledger", effect="charge", rows=2, attempts=(1, 2), statuses=("applied", "duplicate")
    )
    assert mismatch(once, t) is None
    both = LedgerRows(
        source="ledger", effect="charge", rows=2, attempts=(1, 2), statuses=("applied", "applied")
    )
    assert mismatch(both, t) == "2 charge rows, attempts [1, 2], statuses ['applied', 'duplicate']"
    # Without predicted statuses the rows match as before, whatever their statuses.
    plain = LedgerRows(source="ledger", effect="charge", rows=2, attempts=(1, 2))
    assert mismatch(plain, t) is None


def test_rows_without_a_status_or_sink_seq_still_match_as_before(tmp_path: Path) -> None:
    t = trial(tmp_path, [{"effect_key": "charge:t1", "attempt": 1}])
    assert mismatch(LedgerRows(source="ledger", effect="charge", rows=1), t) is None
    predicted = LedgerRows(source="ledger", effect="charge", rows=1, statuses=("applied",))
    assert mismatch(predicted, t) == "1 charge row, attempts [1], statuses ['None']"
