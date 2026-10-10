"""Signal faults on worker processes: SIGKILL, SIGSTOP and SIGCONT, SIGTERM.

POSIX only: Windows has no SIGSTOP or SIGCONT, and workers run on Linux (on other hosts, inside
a Linux container). The target is always a child the orchestrator spawned.
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
    """SIGKILL. Returns the exit status once the process has been reaped (-9).

    Any other status means the process ended some other way first, so the kill did nothing and
    FaultError is raised.
    """
    number = _signal("SIGKILL")

    def apply() -> Data:
        _send(process, "SIGKILL")
        returncode = process.wait(timeout)
        # -9 also results if something else SIGKILLed the child first (the OOM killer, say)
        # and our signal reached the zombie; the status cannot tell the two apart.
        if returncode != -number:
            raise FaultError(f"pid {process.pid} exited with {returncode}, not from SIGKILL")
        return {"returncode": returncode}

    data: Data = {"fault": "kill", "signal": "SIGKILL", "pid": process.pid}
    return _returncode(inject_instant(history, fault_id, data, apply))


def terminate(history: HistoryWriter, process: Process, fault_id: str, grace: float = 10) -> int:
    """SIGTERM, then SIGKILL if the process outlives the grace period."""
    _signal("SIGTERM")

    def apply() -> Data:
        _send(process, "SIGTERM")
        try:
            return {"returncode": process.wait(grace), "escalated": False}
        except subprocess.TimeoutExpired:
            if process.poll() is not None:  # it exited just as the grace period ended
                return {"returncode": process.returncode, "escalated": False}
            _send(process, "SIGKILL")
            return {"returncode": process.wait(grace), "escalated": True}

    data: Data = {"fault": "terminate", "signal": "SIGTERM", "pid": process.pid, "grace_s": grace}
    return _returncode(inject_instant(history, fault_id, data, apply))


def stop(history: HistoryWriter, process: Process, fault_id: str) -> ActiveFault:
    """SIGSTOP; healing sends SIGCONT, or records the exit status if the process has died in the
    meantime (a stopped process can still be killed, and a pause-then-kill schedule heals both)."""
    _signal("SIGSTOP")

    def apply() -> Data:
        _send(process, "SIGSTOP")
        return {}

    def heal() -> Data:
        if process.poll() is None:
            try:
                _send(process, "SIGCONT")
                return {"signal": "SIGCONT"}
            except FaultError:
                if process.poll() is None:  # still running: the SIGCONT itself failed
                    raise
        return {"exited": process.returncode}

    data: Data = {"fault": "stop", "signal": "SIGSTOP", "pid": process.pid}
    return inject(history, fault_id, data, apply, heal)
