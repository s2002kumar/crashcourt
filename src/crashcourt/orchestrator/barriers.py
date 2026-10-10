"""Barriers: which pause each failpoint hit belongs to, which are held, and which steps are due.

A failpoint step with `pause: B` arms its worker to park the matching call. When the worker
reports the hit, the hit holds barrier B until a step releases it or the worker dies. A step
triggered by `all_paused: [B1, B2]` is due the first time all of them are held at once; it
fires once. Nothing here waits or looks at a clock: steps fire on hits alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from crashcourt.spec.protocol import ArmMessage, Hit
from crashcourt.spec.scenario import BarrierStep, FailpointStep, Pause, Scenario, arm_of


class ScheduleError(Exception):
    """A worker reported a hit the schedule did not arm, or reported it twice."""


@dataclass(frozen=True)
class Held:
    barrier: str
    worker: str
    #: The seq of the worker's fp.hit; fp.release names it.
    hit_seq: int
    #: The op whose call is parked.
    op_id: str | None = None


class BarrierBook:
    def __init__(self, scenario: Scenario) -> None:
        self._failpoint_steps = scenario.failpoint_steps()
        self._pauses: list[tuple[str, FailpointStep]] = [
            (step.do.pause, step) for _, step in self._failpoint_steps if isinstance(step.do, Pause)
        ]
        self._barrier_steps = scenario.barrier_steps()
        self._held: dict[str, Held] = {}
        self._seen: set[str] = set()
        self._fired: list[int] = []

    def arms(self, worker: str) -> list[ArmMessage]:
        """The arm messages for `worker`, in schedule order."""
        messages: list[ArmMessage] = []
        for _, step in self._failpoint_steps:
            if step.on.worker != worker:
                continue
            arm = arm_of(step)
            messages.append(
                ArmMessage(
                    point=arm.point,
                    action=arm.action,
                    op_id=arm.op_id,
                    occurrence=arm.occurrence,
                    signal=arm.signal,
                    exit_code=arm.exit_code,
                )
            )
        return messages

    def hit(self, worker: str, hit: Hit) -> Held:
        """Record a parked call; returns the barrier it holds."""
        for barrier, step in self._pauses:
            on = step.on
            if (
                on.worker == worker
                and on.fp == hit.fp
                and (on.op is None or on.op == hit.op_id)
                and on.occurrence == hit.occurrence
            ):
                if barrier in self._seen:
                    raise ScheduleError(f"{worker} reported barrier {barrier} twice")
                self._seen.add(barrier)
                held = Held(barrier, worker, hit.hit_seq, hit.op_id)
                self._held[barrier] = held
                return held
        raise ScheduleError(
            f"{worker} parked at {hit.fp} (op {hit.op_id}, occurrence {hit.occurrence}), "
            "where no step pauses"
        )

    def due(self) -> list[tuple[int, BarrierStep]]:
        """Steps whose barriers are all held now and that have not fired; they are marked fired."""
        ready = [
            (index, step)
            for index, step in self._barrier_steps
            if index not in self._fired and all(b in self._held for b in step.on.all_paused)
        ]
        self._fired.extend(index for index, _ in ready)
        return ready

    def release(self, barriers: tuple[str, ...]) -> list[Held]:
        """Stop holding these barriers; returns what each one held."""
        return [self._held.pop(barrier) for barrier in barriers]

    def drop_worker(self, worker: str) -> list[Held]:
        """A dead worker holds nothing any more."""
        gone = [held for held in self._held.values() if held.worker == worker]
        for held in gone:
            del self._held[held.barrier]
        return gone

    def parked(self, op_id: str) -> bool:
        """Whether a call of this op is held at a barrier right now."""
        return any(held.op_id == op_id for held in self._held.values())

    @property
    def held(self) -> list[Held]:
        return sorted(self._held.values(), key=lambda h: h.barrier)

    @property
    def fired(self) -> list[int]:
        """Indices of the all_paused steps that fired, in firing order."""
        return list(self._fired)

    @property
    def unfired(self) -> list[int]:
        """Indices of the all_paused steps that never fired."""
        return [index for index, _ in self._barrier_steps if index not in self._fired]
