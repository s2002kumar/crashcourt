"""Signal faults on worker processes (F01, F03, F15): SIGKILL, SIGSTOP and SIGCONT, SIGTERM.

POSIX only: Windows has no SIGSTOP or SIGCONT, and the spec runs workers on Linux (on other
hosts, inside a Linux container). The target is always a child the orchestrator spawned.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

from crashcourt.faults.base import ActiveFault, Data, FaultError, inject, inject_instant
from crashcourt.history.writer import HistoryWriter

Process = subprocess.Popen[Any]


def _signal(name: str) -> int:
    if sys.platform == "win32":
        raise FaultError(f"{name} needs a POSIX host; run workers on Linux")
    return int(getattr(signal, name))


def _send(process: Process, name: str) -> None:
    number = _signal(name)
    if process.poll() is not None:
        raise FaultError(f"pid {process.pid} already exited with {process.returncode}")
    os.kill(process.pid, number)


def _returncode(observed: Data) -> int:
    value = observed.get("returncode")
    return value if isinstance(value, int) else 0


def kill(history: HistoryWriter, process: Process, fault_id: str, timeout: float = 10) -> int:
    """SIGKILL (F01). Returns the exit status once the process has been reaped (-9)."""
    _signal("SIGKILL")

    def apply() -> Data:
        _send(process, "SIGKILL")
        return {"returncode": process.wait(timeout)}

    data: Data = {"fault": "kill", "signal": "SIGKILL", "pid": process.pid}
    return _returncode(inject_instant(history, fault_id, data, apply))


def terminate(history: HistoryWriter, process: Process, fault_id: str, grace: float = 10) -> int:
    """SIGTERM, then SIGKILL if the process outlives the grace period (F15)."""
    _signal("SIGTERM")

    def apply() -> Data:
        _send(process, "SIGTERM")
        try:
            return {"returncode": process.wait(grace), "escalated": False}
        except subprocess.TimeoutExpired:
            _send(process, "SIGKILL")
            return {"returncode": process.wait(grace), "escalated": True}

    data: Data = {"fault": "terminate", "signal": "SIGTERM", "pid": process.pid, "grace_s": grace}
    return _returncode(inject_instant(history, fault_id, data, apply))


def stop(history: HistoryWriter, process: Process, fault_id: str) -> ActiveFault:
    """SIGSTOP (F03); healing sends SIGCONT."""
    _signal("SIGSTOP")

    def apply() -> Data:
        _send(process, "SIGSTOP")
        return {}

    def heal() -> Data:
        _send(process, "SIGCONT")
        return {"signal": "SIGCONT"}

    data: Data = {"fault": "stop", "signal": "SIGSTOP", "pid": process.pid}
    return inject(history, fault_id, data, apply, heal)
