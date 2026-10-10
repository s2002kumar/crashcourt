"""The decoded values the effect invariants need, read from each thread's head lineage.

Decoded before any predicate runs: a value that fails strict decoding, or that does not have the
shape the workload's effect model declares, makes the trial INVALID (trigger 7) and never becomes
an anomaly. Only the root namespace is read; W1 and W5 have no subgraphs.
"""

from __future__ import annotations

from dataclasses import dataclass

from crashcourt.checker.decode import decode
from crashcourt.checker.effect_models import EffectModel
from crashcourt.checker.facts import NULL_TASK_ID, RESUME, checkpoint_pointer
from crashcourt.checker.lineage import ChannelReader, Lineage, LineageKey

Entry = tuple[str, int]


@dataclass(frozen=True)
class ThreadState:
    thread_id: str
    head: str | None
    head_path: tuple[str, ...]
    #: The head checkpoint's executed entries.
    head_executed: tuple[Entry, ...]
    #: Every executed entry on the head's ancestor path, with the checkpoint it was read from.
    lineage_executed: dict[Entry, str]
    head_decision: str | None
    #: The decision each RESUME write on the head path holds, with a pointer to the write.
    resume_decisions: tuple[tuple[str, str], ...]


def decode_states(
    threads: tuple[str, ...],
    lineages: dict[LineageKey, Lineage],
    reader: ChannelReader,
    model: EffectModel,
) -> dict[str, ThreadState]:
    """Raises DecodeError or ShapeError when a needed value cannot be trusted."""
    states: dict[str, ThreadState] = {}
    for thread_id in threads:
        lineage = lineages.get((thread_id, ""))
        if lineage is None or lineage.head is None:
            states[thread_id] = ThreadState(thread_id, None, (), (), {}, None, ())
            continue
        path = lineage.head_path()
        executed: dict[Entry, str] = {}
        head_executed: tuple[Entry, ...] = ()
        head_decision: str | None = None
        for checkpoint_id in path:
            row = lineage.rows[checkpoint_id]
            where = checkpoint_pointer(thread_id, "", checkpoint_id)
            if model.executed_channel is not None:
                present, value = reader.value(row, model.executed_channel)
                entries = model.executed_entries(value) if present else ()
                for entry in entries:
                    executed.setdefault(entry, where)
                if checkpoint_id == lineage.head:
                    head_executed = entries
            if model.decision_channel is not None and checkpoint_id == lineage.head:
                present, value = reader.value(row, model.decision_channel)
                head_decision = model.decision_of(value) if present and value is not None else None
        resumes: list[tuple[str, str]] = []
        if model.decisions:
            on_path = set(path)
            for checkpoint_id, writes in lineage.writes.items():
                if checkpoint_id not in on_path:
                    continue
                for write in writes:
                    # The RESUME write for the interrupted task (founder ruling, 2026-10-10), not
                    # the null task's copy of the Command's value.
                    if write.channel == RESUME and write.task_id != NULL_TASK_ID:
                        decision = model.decision_of(decode(write.type, write.blob))
                        resumes.append(
                            (
                                decision,
                                f"snapshot#write={thread_id}||{checkpoint_id}|"
                                f"{write.task_id}|{write.idx}",
                            )
                        )
        states[thread_id] = ThreadState(
            thread_id,
            lineage.head,
            path,
            head_executed,
            executed,
            head_decision,
            tuple(resumes),
        )
    return states
