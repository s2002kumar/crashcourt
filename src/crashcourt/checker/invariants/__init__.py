"""Layer A: one pure predicate per invariant, in the order results list them.

A predicate reads the trusted records through the checker's context and returns an Observation.
It never names an outcome or a clause; crashcourt.checker.classify does that.
"""

from __future__ import annotations

from collections.abc import Callable

from crashcourt.checker.invariants._common import Context
from crashcourt.checker.invariants.durability import i1a, i1b, i11
from crashcourt.checker.invariants.effects import i3_alarm, i7a, i7b, i7c, i8
from crashcourt.checker.invariants.lineage import i5a, i5b, i5c, i6a, i6b
from crashcourt.checker.invariants.writes import i2, i4
from crashcourt.checker.observations import Observation

Predicate = Callable[[Context], Observation]

PREDICATES: tuple[tuple[str, Predicate], ...] = (
    ("I1a", i1a),
    ("I1b", i1b),
    ("I2", i2),
    ("I4", i4),
    ("I5a", i5a),
    ("I5b", i5b),
    ("I5c", i5c),
    ("I6a", i6a),
    ("I6b", i6b),
    ("I7a", i7a),
    ("I7b", i7b),
    ("I7c", i7c),
    ("I8", i8),
    ("I11", i11),
)
INVARIANTS: tuple[str, ...] = tuple(name for name, _ in PREDICATES)

__all__ = ["INVARIANTS", "PREDICATES", "Context", "Predicate", "i3_alarm"]
