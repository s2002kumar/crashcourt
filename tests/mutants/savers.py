"""LABELED SAVER MUTANTS M1-M5, for control C5 only. Test code: never a target, never evidence.

Each mutant is the pinned AsyncPostgresSaver with one defect, from the specification's section 13.
A worker runs one only when the runner names it (`RunOptions.saver_mutant`), which it refuses for a
pre-registered or canonical batch and for any batch written under results/. The workers call the
async methods only, so only those carry the defects.

C5 asks that the frozen checker catch each mutant through its target invariant in at least 19 of 20
trials. The targets were fixed before any mutant ran (issue #30):

| Mutant | Defect | Target |
| --- | --- | --- |
| M1 | a put_writes call that writes two or more rows does not store its second row | I7a |
| M2 | get_tuple by checkpoint id returns None | I5a |
| M3 | put stores no parent | I5a |
| M4 | the second put of each op is written in a transaction whose commit fails; the error is
  swallowed and the put reports success | I1b |
| M5 | the latest-checkpoint read returns the head's parent | I6b |
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
)
from langgraph.checkpoint.postgres import _ainternal  # pyright: ignore[reportPrivateUsage]
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from crashcourt.failpoints.context import current_op


class M1DropsSecondWrite(AsyncPostgresSaver):
    """LABELED MUTANT M1: a put_writes call writing two or more rows does not store the second."""

    def _dump_writes(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        task_id: str,
        task_path: str,
        writes: Sequence[tuple[str, Any]],
    ) -> list[tuple[str, str, str, str, str, int, str, str, bytes]]:
        rows = super()._dump_writes(
            thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, writes
        )
        return rows[:1] + rows[2:]


class M2NoneById(AsyncPostgresSaver):
    """LABELED MUTANT M2: a read of a checkpoint by its id finds nothing."""

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        if get_checkpoint_id(config):
            return None
        return await super().aget_tuple(config)


class M3NoParent(AsyncPostgresSaver):
    """LABELED MUTANT M3: put stores the checkpoint without its parent."""

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        configurable = {
            k: v for k, v in config.get("configurable", {}).items() if k != "checkpoint_id"
        }
        orphan: RunnableConfig = {**config, "configurable": configurable}
        return await super().aput(orphan, checkpoint, metadata, new_versions)


class _CommitFailed(Exception):
    """Stands for the error a failed COMMIT raises."""


class M4SwallowsCommitError(AsyncPostgresSaver):
    """LABELED MUTANT M4: the second put of each op is written inside a transaction whose commit
    fails, so nothing of it is stored; the error is swallowed, and the put reports success."""

    #: Which put of an op fails, counted per op as the failpoints count calls.
    FAILING_PUT = 2

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._puts: dict[str | None, int] = {}
        self._count = threading.Lock()

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        op = current_op()
        with self._count:
            self._puts[op] = number = self._puts.get(op, 0) + 1
        if number != self.FAILING_PUT:
            return await super().aput(config, checkpoint, metadata, new_versions)
        stored: RunnableConfig | None = None
        try:
            async with _ainternal.get_connection(self.conn) as conn, conn.transaction():
                # The same statements, on one connection, inside a transaction that never commits.
                inner = AsyncPostgresSaver(conn, serde=self.serde)
                stored = await inner.aput(config, checkpoint, metadata, new_versions)
                raise _CommitFailed("M4: the commit of this put failed")
        except _CommitFailed:
            pass  # the defect: the failure is swallowed
        assert stored is not None
        return stored


class M5StaleHead(AsyncPostgresSaver):
    """LABELED MUTANT M5: a read of the latest checkpoint returns the latest one's parent."""

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        latest = await super().aget_tuple(config)
        if get_checkpoint_id(config) or latest is None or latest.parent_config is None:
            return latest
        return await super().aget_tuple(latest.parent_config)


@dataclass(frozen=True)
class Mutant:
    id: str
    saver: type[AsyncPostgresSaver]
    defect: str
    #: The invariant that must catch it (fixed in issue #30 before any mutant ran).
    target: str


CATALOG: tuple[Mutant, ...] = (
    Mutant(
        "M1",
        M1DropsSecondWrite,
        "a put_writes call that writes two or more rows does not store its second row",
        "I7a",
    ),
    Mutant("M2", M2NoneById, "get_tuple by checkpoint id returns None", "I5a"),
    Mutant("M3", M3NoParent, "put stores no parent", "I5a"),
    Mutant(
        "M4",
        M4SwallowsCommitError,
        "the second put of each op is written in a transaction whose commit fails; the error is "
        "swallowed and the put reports success",
        "I1b",
    ),
    Mutant("M5", M5StaleHead, "the latest-checkpoint read returns the head's parent", "I6b"),
)
#: What the worker loads by name (crashcourt.worker.main.MUTANTS_MODULE).
MUTANTS: dict[str, type[AsyncPostgresSaver]] = {m.id: m.saver for m in CATALOG}
