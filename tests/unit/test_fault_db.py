from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.faults import db
from crashcourt.faults.base import FaultError
from crashcourt.history.writer import HistoryWriter, RunIdentity


@pytest.mark.parametrize("wait_ms", [0, -1])
def test_termination_needs_a_positive_wait(tmp_path: Path, wait_ms: int) -> None:
    run = RunIdentity(batch="DEMO-faults", cell="DEMO-cell", trial=0, seed="s")
    with (
        HistoryWriter(tmp_path / "o.jsonl", run=run, proc="orchestrator") as history,
        pytest.raises(FaultError, match="positive wait_ms"),
    ):
        db.terminate_backends(
            history, "postgresql://u@127.0.0.1/db", "t-1", application_name="x", wait_ms=wait_ms
        )
