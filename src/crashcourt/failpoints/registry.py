"""Named failpoints and the arms that fire at them.

An arm names a point and an action, and optionally narrows which hit it fires on: the worker,
the client operation, the thread, and the occurrence (the nth matching call, counting from 1).
Each arm fires at most once. Arming an unknown point, action, or signal is a spec error, and so
is a call on which two arms reach their occurrence: only one action can run there, so the other
arm would never fire and the trial would run a different schedule from the one declared.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal, get_args

Point = Literal[
    "put.before",
    "put.after",
    "put_writes.before",
    "put_writes.after",
    "get_tuple.before",
    "get_tuple.after",
    "list.before",
    "list.after",
]
POINTS: tuple[str, ...] = get_args(Point)

Action = Literal["none", "pause", "exit", "signal"]
ACTIONS: tuple[str, ...] = get_args(Action)

#: Signals a worker may send itself at a failpoint (POSIX names; workers run on Linux).
Signal = Literal["SIGKILL", "SIGTERM", "SIGSTOP", "SIGINT", "SIGQUIT", "SIGABRT", "SIGSEGV"]
SIGNALS: tuple[str, ...] = get_args(Signal)


class FailpointError(Exception):
    """A failpoint cannot be honored as armed (a spec error); the trial is INVALID."""


@dataclass(frozen=True)
class Arm:
    point: Point
    action: Action
    worker: str | None = None
    op_id: str | None = None
    thread_id: str | None = None
    occurrence: int = 1
    #: For action "signal": the signal the worker sends itself, e.g. "SIGKILL" or "SIGSTOP".
    signal: Signal | None = None
    #: For action "exit": the status passed to os._exit.
    exit_code: int = 1

    def __post_init__(self) -> None:
        if self.point not in POINTS:
            raise ValueError(f"unknown failpoint {self.point!r}; known: {', '.join(POINTS)}")
        if self.action not in ACTIONS:
            raise ValueError(f"unknown action {self.action!r}; known: {', '.join(ACTIONS)}")
        if self.occurrence < 1:
            raise ValueError("occurrence counts from 1")
        if (self.action == "signal") != (self.signal is not None):
            raise ValueError("a signal name is required for action 'signal', and only for it")
        if self.signal is not None and self.signal not in SIGNALS:
            raise ValueError(f"unknown signal {self.signal!r}; known: {', '.join(SIGNALS)}")
        if not 0 <= self.exit_code <= 255:
            raise ValueError("an exit status is between 0 and 255")

    def matches(self, point: str, worker: str, op_id: str | None, thread_id: str | None) -> bool:
        return (
            point == self.point
            and (self.worker is None or self.worker == worker)
            and (self.op_id is None or self.op_id == op_id)
            and (self.thread_id is None or self.thread_id == thread_id)
        )


@dataclass(frozen=True)
class Firing:
    arm: Arm
    occurrence: int


class FailpointRegistry:
    """The arms for one worker process. Thread-safe: savers are called from many threads."""

    def __init__(self, worker: str) -> None:
        self.worker = worker
        self._lock = threading.Lock()
        self._arms: list[Arm] = []
        self._counts: dict[int, int] = {}
        self._fired: set[int] = set()

    def arm(self, arm: Arm) -> None:
        with self._lock:
            self._arms.append(arm)
            self._counts[len(self._arms) - 1] = 0

    def disarm_all(self) -> None:
        with self._lock:
            self._arms.clear()
            self._counts.clear()
            self._fired.clear()

    @property
    def armed(self) -> bool:
        with self._lock:
            return len(self._fired) < len(self._arms)

    def check(self, point: str, op_id: str | None, thread_id: str | None) -> Firing | None:
        """Count this call against every matching arm; return the one that fires now, if any.

        Raises FailpointError when two or more arms reach their occurrence on this call.
        """
        with self._lock:
            due: list[int] = []
            for index, arm in enumerate(self._arms):
                if index in self._fired or not arm.matches(point, self.worker, op_id, thread_id):
                    continue
                self._counts[index] += 1
                if self._counts[index] == arm.occurrence:
                    due.append(index)
            self._fired.update(due)
            if len(due) > 1:
                arms = "; ".join(repr(self._arms[i]) for i in due)
                raise FailpointError(f"{len(due)} arms fire on one {point} call: {arms}")
            if not due:
                return None
            return Firing(self._arms[due[0]], self._counts[due[0]])
