"""Workload effect models: each workload's effects and state. Frozen with the checker.

From the red-team specification 2.1:

- W1 (approval): plan, approval (calls interrupt()), execute, record. Approve fires exactly one
  `charge:<thread>` from execute, after the interrupt; reject fires none. Record writes
  `executed = [(effect_key, attempt)]` and `decision` into state.
- W1 misuse: as W1, plus `precheck:<thread>` from approval before interrupt(), on every attempt of
  approval, whatever the decision.
- W5 (continuation): two user turns, no effects; state holds the message list.

The state channel names and value shapes below follow the specification's text and match T-09's
real fault-free histories (research/evidence/t-10/); C1 and C9 validate them per batch. The models
are frozen with the checker: their hashes are in FROZEN.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from crashcourt.checker.artifacts import OpKind, OpSpec, Workload


class ShapeError(ValueError):
    """A decoded value does not have the shape the effect model declares; the trial is INVALID."""


class EffectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The effect key, with {thread} for the thread id.
    key_template: str
    #: The node that posts the effect.
    node: str
    #: True when the model places the effect before interrupt() in its node.
    before_interrupt: bool
    #: The kinds of client op whose run posts the effect.
    produced_by: tuple[OpKind, ...]
    #: The resume value the effect requires, or None if it is posted whatever the decision.
    requires_decision: str | None = None

    def key(self, thread_id: str) -> str:
        return self.key_template.format(thread=thread_id)


class EffectModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workload: Workload
    effects: tuple[EffectSpec, ...]
    #: The state channel record writes [(effect_key, attempt), ...] into.
    executed_channel: str | None = None
    #: The state channel holding the decision.
    decision_channel: str | None = None
    decisions: tuple[str, ...] = ()
    #: The effect I7c judges against the decision ("a charge row exists iff decision is approve").
    approval_effect: str | None = None
    #: State channels whose values the saver stores as blobs (non-primitive values), for I5c.
    blob_channels: tuple[str, ...] = ()

    def spec_for(self, effect_key: str, thread_id: str) -> EffectSpec | None:
        return next((s for s in self.effects if s.key(thread_id) == effect_key), None)

    def expected_keys(self, op: OpSpec) -> tuple[str, ...]:
        """The effects a successful run of `op` must have produced at least once."""
        return tuple(
            spec.key(op.thread_id)
            for spec in self.effects
            if op.kind in spec.produced_by
            and (spec.requires_decision is None or op.resume == spec.requires_decision)
        )

    def judged_by_i4_only(self, effect_key: str, thread_id: str) -> bool:
        """Effects the model places before interrupt() are exempt from I7 and judged by I4(b)."""
        spec = self.spec_for(effect_key, thread_id)
        return spec is not None and spec.before_interrupt

    def decision_of(self, value: Any) -> str:
        """The decision a RESUME write or the decision channel holds.

        A resume write holds the raw resume value (the null task's write) or the list of values
        interrupt() consumed (the interrupted task's write); W1 interrupts once.
        """
        if isinstance(value, list | tuple):
            items = cast("Sequence[object]", value)
            if len(items) == 1:
                value = items[0]
        if isinstance(value, str) and value in self.decisions:
            return value
        raise ShapeError(f"not a decision of {self.workload}: {value!r}")

    def executed_entries(self, value: Any) -> tuple[tuple[str, int], ...]:
        """The executed channel as (effect_key, attempt) pairs."""
        if not isinstance(value, list | tuple):
            raise ShapeError(f"executed is not a list: {value!r}")
        entries: list[tuple[str, int]] = []
        for item in cast("Sequence[object]", value):
            pair = cast("Sequence[object]", item) if isinstance(item, list | tuple) else ()
            if len(pair) != 2:
                raise ShapeError(f"executed entry is not a pair: {item!r}")
            key, attempt = pair
            if (
                not isinstance(key, str)
                or not isinstance(attempt, int)
                or isinstance(attempt, bool)
            ):
                raise ShapeError(f"executed entry is not (effect_key, attempt): {item!r}")
            if attempt < 1:
                raise ShapeError(f"executed entry has attempt {attempt}: {item!r}")
            entries.append((key, attempt))
        return tuple(entries)


_CHARGE = EffectSpec(
    key_template="charge:{thread}",
    node="execute",
    before_interrupt=False,
    produced_by=("resume",),
    requires_decision="approve",
)
_PRECHECK = EffectSpec(
    key_template="precheck:{thread}",
    node="approval",
    before_interrupt=True,
    produced_by=("start", "resume"),
)

W1 = EffectModel(
    workload="w1",
    effects=(_CHARGE,),
    executed_channel="executed",
    decision_channel="decision",
    decisions=("approve", "reject"),
    approval_effect="charge:{thread}",
    blob_channels=("executed",),
)
W1_MISUSE = W1.model_copy(update={"workload": "w1-misuse", "effects": (_PRECHECK, _CHARGE)})
W5 = EffectModel(workload="w5", effects=(), blob_channels=("messages",))

EFFECT_MODELS: dict[str, EffectModel] = {m.workload: m for m in (W1, W1_MISUSE, W5)}
