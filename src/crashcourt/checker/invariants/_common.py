"""What every Layer A predicate reads, and the thread-level witnesses they all attach."""

from __future__ import annotations

from dataclasses import dataclass

from crashcourt.checker.artifacts import TrialScenario
from crashcourt.checker.effect_models import EffectModel
from crashcourt.checker.facts import TrialFacts, pointer
from crashcourt.checker.lineage import ChannelReader, Lineage, LineageKey
from crashcourt.checker.observations import (
    DISCLAIMED,
    HARNESS_EXPLAINED,
    SINGLE_WRITER,
    STOP_WITNESSED,
    Witness,
)
from crashcourt.checker.state import ThreadState
from crashcourt.spec.events import Event


@dataclass(frozen=True)
class Context:
    scenario: TrialScenario
    facts: TrialFacts
    lineages: dict[LineageKey, Lineage]
    reader: ChannelReader
    model: EffectModel
    states: dict[str, ThreadState]

    def worker_events(self) -> list[Event]:
        by_proc = self.facts.order.by_proc
        return [e for w in self.facts.workers for e in by_proc[w]]


def thread_witnesses(ctx: Context, thread_id: str) -> tuple[Witness, ...]:
    """DISCLAIMED, HARNESS_EXPLAINED, SINGLE_WRITER, and STOP_WITNESSED for one thread."""
    facts = ctx.facts
    disclaimed = facts.disclaimed(thread_id)
    concurrent = facts.concurrency(thread_id)
    harness = facts.harness_events(thread_id)
    writers = set(facts.writers(thread_id))
    stops = tuple(dict.fromkeys(pointer(s.source) for s in facts.stops if s.worker in writers))
    return (
        Witness(name=DISCLAIMED, held=bool(disclaimed), evidence=disclaimed),
        Witness(name=HARNESS_EXPLAINED, held=bool(harness), evidence=harness),
        Witness(name=SINGLE_WRITER, held=not concurrent, evidence=concurrent),
        Witness(name=STOP_WITNESSED, held=bool(stops), evidence=stops),
    )


def trial_witnesses(ctx: Context, threads: set[str]) -> tuple[Witness, ...]:
    """DISCLAIMED for the threads an invariant exercised: held if any of them is disclaimed."""
    evidence: list[str] = []
    for thread_id in sorted(threads):
        evidence.extend(ctx.facts.disclaimed(thread_id))
    return (Witness(name=DISCLAIMED, held=bool(evidence), evidence=tuple(evidence)),)
