"""Per-process history writer: one JSONL line per event, written before `emit` returns.

Each line goes to the kernel with one unbuffered `write` on an append-only descriptor, so a
process killed with SIGKILL loses at most the line it was writing. A machine crash can lose
more; the spec accepts that (section 7), and nothing here calls fsync.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from crashcourt.spec.events import SCHEMA_VERSION, Event, EventKind

_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | getattr(os, "O_BINARY", 0)


if sys.platform == "win32":

    def _host_monotonic_ns() -> int:
        """Windows has no CLOCK_MONOTONIC, and time.monotonic there ticks every 15.6 ms.

        QueryPerformanceCounter (time.perf_counter_ns) is system-wide and fine-grained. Official
        runs are on Linux.
        """
        return time.perf_counter_ns()

else:

    def _host_monotonic_ns() -> int:
        """CLOCK_MONOTONIC: the host-wide clock spec section 9 names for mono_ns."""
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


@dataclass(frozen=True)
class RunIdentity:
    """The fields every event of one trial shares."""

    batch: str
    cell: str
    trial: int
    seed: str


class HistoryWriter:
    """Writes one process's history for one trial. Safe to share between threads."""

    def __init__(self, path: Path, *, run: RunIdentity, proc: str, pid: int | None = None) -> None:
        self.path = path
        self.run = run
        self.proc = proc
        self.pid = os.getpid() if pid is None else pid
        self._fd: int | None = os.open(path, _FLAGS, 0o644)
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, kind: EventKind, **fields: Any) -> Event:
        """Validate, number, and write one event; return it once it is in the file."""
        with self._lock:
            if self._fd is None:
                raise ValueError(f"history writer for {self.path} is closed")
            event = Event(
                v=SCHEMA_VERSION,
                batch=self.run.batch,
                cell=self.run.cell,
                trial=self.run.trial,
                seed=self.run.seed,
                proc=self.proc,
                pid=self.pid,
                seq=self._seq,
                mono_ns=_host_monotonic_ns(),
                wall_ns=time.time_ns(),
                kind=kind,
                **fields,
            )
            _write_all(self._fd, event.to_line().encode("utf-8"))
            self._seq += 1
            return event

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]
