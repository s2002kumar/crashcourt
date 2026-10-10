"""Barriers: how a paused failpoint waits for the orchestrator's release.

T-07 implements `Barrier` over the control protocol (the worker sends `hit`, the orchestrator
answers `release`). `InProcessBarrier` is the test double: waiters block on an event until a
test releases them, and the release is recorded like the orchestrator would record it.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from crashcourt.history.writer import HistoryWriter
from crashcourt.spec.events import Event


class Barrier(Protocol):
    def wait(self, hit: Event) -> None:
        """Block until the orchestrator releases this hit."""
        ...


class InProcessBarrier:
    def __init__(self, orchestrator: HistoryWriter | None = None) -> None:
        self.orchestrator = orchestrator
        self._lock = threading.Condition()
        self._waiting: dict[tuple[str, int], threading.Event] = {}
        self._released: set[tuple[str, int]] = set()

    def wait(self, hit: Event) -> None:
        key = (hit.proc, hit.seq)
        with self._lock:
            gate = self._waiting.setdefault(key, threading.Event())
            if key in self._released:
                gate.set()
            self._lock.notify_all()
        gate.wait()
        with self._lock:
            self._waiting.pop(key, None)

    def waiting(self) -> list[tuple[str, int]]:
        with self._lock:
            return sorted(self._waiting)

    def wait_for_hit(self, timeout: float = 5.0) -> tuple[str, int]:
        """Block until some caller is parked at a barrier; return its (proc, hit seq)."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while not self._waiting:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("no failpoint hit arrived")
                self._lock.wait(remaining)
            return sorted(self._waiting)[0]

    def release(self, proc: str, hit_seq: int) -> None:
        """Release one hit, recording `fp.release` first as the orchestrator does."""
        if self.orchestrator is not None:
            self.orchestrator.emit("fp.release", data={"proc": proc, "hit_seq": hit_seq})
        with self._lock:
            self._released.add((proc, hit_seq))
            gate = self._waiting.get((proc, hit_seq))
        if gate is not None:
            gate.set()
