from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from crashcourt.faults import signals
from crashcourt.faults.base import FaultError
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity


class ExitedOnItsOwn:
    """A process that ended with status 0 just before the SIGKILL reached it."""

    pid = 4242
    returncode: int | None = None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.mark.skipif(sys.platform == "win32", reason="signal faults need a POSIX host")
def test_kill_that_did_not_end_the_process_is_a_fault_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signals.os, "kill", lambda pid, number: None)
    run = RunIdentity(batch="DEMO-faults", cell="DEMO-cell", trial=0, seed="s")
    path = tmp_path / "orchestrator.jsonl"
    process: Any = ExitedOnItsOwn()
    with (
        HistoryWriter(path, run=run, proc="orchestrator") as history,
        pytest.raises(FaultError, match="not from SIGKILL"),
    ):
        signals.kill(history, process, "kill-1")
    heal = read_history(path).events[-1]
    assert (heal.kind, heal.status) == ("fault.heal", "fail")
    assert heal.error is not None and "exited with 0" in heal.error
