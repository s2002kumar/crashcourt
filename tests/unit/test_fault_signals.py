from __future__ import annotations

import subprocess
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


@pytest.mark.skipif(sys.platform == "win32", reason="signal faults need a POSIX host")
def test_healing_a_stop_after_the_process_was_killed_records_its_exit(tmp_path: Path) -> None:
    run = RunIdentity(batch="DEMO-faults", cell="DEMO-cell", trial=0, seed="s")
    path = tmp_path / "orchestrator.jsonl"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with (
        HistoryWriter(path, run=run, proc="orchestrator") as history,
        signals.stop(history, child, "stop-1"),
    ):
        assert signals.kill(history, child, "kill-1") == -9
    stop_heal = read_history(path).events[-1]
    assert (stop_heal.kind, stop_heal.fault_id, stop_heal.status) == ("fault.heal", "stop-1", "ok")
    assert stop_heal.data == {"exited": -9}
