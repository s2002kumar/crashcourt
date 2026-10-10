"""The trusted order: happens-before over every process's events.

Within a process, seq is a total order. Across processes the only edges are T-04's causal edges
(crashcourt.history.merge.causal_edges): dispatch, barrier, effect, exit, and result. Two events
no chain of edges connects are unordered, and the checker treats them as unordered: it never
breaks a tie with a clock. This is why the checker does not call `merge()`, which linearizes
with the monotonic clock for display.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Mapping, Sequence

from crashcourt.history.merge import Ref, causal_edges
from crashcourt.history.reader import HistoryError
from crashcourt.spec.events import Event


class Order:
    """Happens-before, by vector clocks over the processes' histories and the causal edges."""

    def __init__(self, by_proc: Mapping[str, Sequence[Event]]) -> None:
        self.by_proc: dict[str, tuple[Event, ...]] = {p: tuple(e) for p, e in by_proc.items()}
        predecessors: dict[Ref, list[Ref]] = defaultdict(list)
        successors: dict[Ref, list[Ref]] = defaultdict(list)
        indegree: dict[Ref, int] = {}
        for proc, events in self.by_proc.items():
            for event in events:
                ref = Ref(proc, event.seq)
                indegree[ref] = 0
                if event.seq:
                    previous = Ref(proc, event.seq - 1)
                    predecessors[ref].append(previous)
                    successors[previous].append(ref)
                    indegree[ref] += 1
        for before, after in sorted(causal_edges(self.by_proc)):
            predecessors[after].append(before)
            successors[before].append(after)
            indegree[after] += 1

        # Kahn's algorithm; ties broken by (proc, seq) only, so the result is deterministic.
        ready = [ref for ref, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        clocks: dict[Ref, dict[str, int]] = {}
        while ready:
            ref = heapq.heappop(ready)
            clock: dict[str, int] = {}
            for previous in predecessors[ref]:
                for proc, seq in clocks[previous].items():
                    if clock.get(proc, -1) < seq:
                        clock[proc] = seq
            clock[ref.proc] = ref.seq
            clocks[ref] = clock
            for after in successors[ref]:
                indegree[after] -= 1
                if indegree[after] == 0:
                    heapq.heappush(ready, after)
        if len(clocks) != len(indegree):
            stuck = sorted(ref for ref in indegree if ref not in clocks)[:5]
            raise HistoryError(f"causal edges form a cycle; first events involved: {stuck}")
        self._clocks = clocks

    def before(self, a: Ref, b: Ref) -> bool:
        """Whether a happens before b. False for a == b and for unordered events."""
        return a != b and self._clocks[b].get(a.proc, -1) >= a.seq

    def event(self, ref: Ref) -> Event:
        return self.by_proc[ref.proc][ref.seq]


def ref(event: Event) -> Ref:
    return Ref(event.proc, event.seq)
