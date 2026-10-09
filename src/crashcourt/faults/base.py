"""The inject and heal bookkeeping shared by every fault primitive.

`fault.inject` is written before the fault is applied, so it precedes every consequence. Every
inject gets exactly one `fault.heal`: when the fault is healed, or at once if the fault is
instantaneous or applying it failed. The heal carries what was observed (an exit status, how
many backends ended, how long a restart took), so the history holds it, not only the caller. A
heal after a failure has status fail and the error in the envelope's error field.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import TracebackType
from typing import Self

from pydantic import JsonValue

from crashcourt.history.writer import HistoryWriter

Data = dict[str, JsonValue]


class FaultError(Exception):
    """A fault could not be injected or healed as requested; the trial is INVALID.

    `observed` is what the attempt saw before it failed; the failed `fault.heal` records it.
    """

    def __init__(self, message: str, observed: Data | None = None) -> None:
        super().__init__(message)
        self.observed: Data = dict(observed or {})


class ActiveFault:
    """A fault that has taken effect. `heal()` ends it and writes `fault.heal`, exactly once."""

    def __init__(
        self,
        history: HistoryWriter,
        fault_id: str,
        observed: Data,
        heal: Callable[[], Data | None] | None,
    ) -> None:
        self.history = history
        self.fault_id = fault_id
        self.observed = observed
        self._heal = heal
        self._healed = False
        self._lock = threading.Lock()

    @property
    def healed(self) -> bool:
        return self._healed

    def heal(self) -> None:
        with self._lock:
            if self._healed:
                return
            self._healed = True
            try:
                result = self._heal() if self._heal is not None else None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                seen = {**self.observed, **_observed(exc)}
                _close(self.history, self.fault_id, seen, error)
                raise FaultError(f"healing {self.fault_id} failed: {error}", seen) from exc
            _close(self.history, self.fault_id, {**self.observed, **(result or {})})

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.heal()


def _observed(exc: Exception) -> Data:
    return exc.observed if isinstance(exc, FaultError) else {}


def _close(history: HistoryWriter, fault_id: str, data: Data, error: str | None = None) -> None:
    status = "ok" if error is None else "fail"
    history.emit("fault.heal", fault_id=fault_id, status=status, error=error, data=data)


def inject(
    history: HistoryWriter,
    fault_id: str,
    data: Data,
    apply: Callable[[], Data | None],
    heal: Callable[[], Data | None] | None = None,
) -> ActiveFault:
    """Write `fault.inject`, apply the fault, and return a handle whose `heal()` ends it."""
    history.emit("fault.inject", fault_id=fault_id, data=data)
    try:
        observed = apply() or {}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _close(history, fault_id, _observed(exc), error)
        raise FaultError(f"injecting {fault_id} failed: {error}", _observed(exc)) from exc
    return ActiveFault(history, fault_id, observed, heal)


def inject_instant(
    history: HistoryWriter, fault_id: str, data: Data, apply: Callable[[], Data | None]
) -> Data:
    """A fault with no duration (a kill, a backend termination): inject, apply, heal at once.

    The heal carries what `apply` observed."""
    fault = inject(history, fault_id, data, apply)
    fault.heal()
    return fault.observed
