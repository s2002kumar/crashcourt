"""Direct-saver audit of langgraph-checkpoint-postgres 3.1.2 on a real Postgres (T-03, gate G0).

Each test states a property read from the saver's source (research/source-audit.md) and checks it
through the public API, with the database itself as the witness (rows, xmin, commit timestamps).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any, LiteralString

import psycopg
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.serde.types import RESUME

from ._checkpoints import checkpoint, config, metadata, new_thread

pytestmark = pytest.mark.integration


def rows(dsn: str, sql: LiteralString, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        return conn.execute(sql, params).fetchall()


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    # psycopg's async driver needs a selector loop; Windows defaults to the proactor loop.
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


Putter = Callable[[str, RunnableConfig, Checkpoint, CheckpointMetadata, ChannelVersions], object]


def put_sync(
    dsn: str,
    cfg: RunnableConfig,
    cp: Checkpoint,
    md: CheckpointMetadata,
    versions: ChannelVersions,
    *,
    pipeline: bool,
) -> object:
    with PostgresSaver.from_conn_string(dsn, pipeline=pipeline) as saver:
        return saver.put(cfg, cp, md, versions)


def put_async(
    dsn: str, cfg: RunnableConfig, cp: Checkpoint, md: CheckpointMetadata, versions: ChannelVersions
) -> object:
    async def run() -> object:
        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            return await saver.aput(cfg, cp, md, versions)

    return run_async(run())


PUTTERS: dict[str, Putter] = {
    "sync": partial(put_sync, pipeline=False),
    "sync-pipeline": partial(put_sync, pipeline=True),
    "async": put_async,
}


def lineage(dsn: str, thread_id: str) -> dict[str, str | None]:
    return dict(
        rows(
            dsn,
            "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints WHERE thread_id = %s",
            thread_id,
        )
    )


def stored_writes(dsn: str, thread_id: str) -> dict[tuple[str, str], object]:
    """Pending writes as the database holds them, decoded with the savers' default serializer."""
    serde = JsonPlusSerializer()
    found = rows(
        dsn,
        "SELECT task_id, channel, type, blob FROM checkpoint_writes WHERE thread_id = %s",
        thread_id,
    )
    return {
        (task, channel): serde.loads_typed((kind, bytes(blob)))
        for task, channel, kind, blob in found
    }


def test_two_sync_savers_fork_one_parent(dsn: str) -> None:
    """put never checks that its parent is still the head, so two writers fork the thread.

    This replicates, at the saver level, the fork that langgraph#8796 and the xbstack two-process
    repro show end to end; it is a replication, not a new finding. xbstack's repro:
    https://github.com/xbstack/langgraph-same-thread-concurrent-writer-repro
    """
    thread = new_thread()
    root = checkpoint(0, {"n": 0}, {"n": "1"})
    with PostgresSaver.from_conn_string(dsn) as a, PostgresSaver.from_conn_string(dsn) as b:
        a.put(config(thread), root, metadata(0), {"n": "1"})
        head_a, head_b = a.get_tuple(config(thread)), b.get_tuple(config(thread))
        assert head_a is not None and head_b is not None
        assert head_a.checkpoint["id"] == head_b.checkpoint["id"] == root["id"]
        child_a = checkpoint(1, {"n": 1}, {"n": "2"})
        child_b = checkpoint(1, {"n": 2}, {"n": "2"})
        a.put(head_a.config, child_a, metadata(1), {"n": "2"})
        b.put(head_b.config, child_b, metadata(1), {"n": "2"})
        latest = a.get_tuple(config(thread))
    assert lineage(dsn, thread) == {
        root["id"]: None,
        child_a["id"]: root["id"],
        child_b["id"]: root["id"],
    }
    assert latest is not None
    assert latest.checkpoint["id"] == max(child_a["id"], child_b["id"])


def test_two_async_savers_fork_one_parent(dsn: str) -> None:
    """The same fork through AsyncPostgresSaver (replicating langgraph#8796, as above)."""
    thread = new_thread()
    root = checkpoint(0, {"n": 0}, {"n": "1"})
    child_a = checkpoint(1, {"n": 1}, {"n": "2"})
    child_b = checkpoint(1, {"n": 2}, {"n": "2"})

    async def run() -> None:
        async with (
            AsyncPostgresSaver.from_conn_string(dsn) as a,
            AsyncPostgresSaver.from_conn_string(dsn) as b,
        ):
            await a.aput(config(thread), root, metadata(0), {"n": "1"})
            head_a, head_b = await a.aget_tuple(config(thread)), await b.aget_tuple(config(thread))
            assert head_a is not None and head_b is not None
            await a.aput(head_a.config, child_a, metadata(1), {"n": "2"})
            await b.aput(head_b.config, child_b, metadata(1), {"n": "2"})

    run_async(run())
    assert lineage(dsn, thread) == {
        root["id"]: None,
        child_a["id"]: root["id"],
        child_b["id"]: root["id"],
    }


def test_latest_is_the_largest_id_not_the_last_commit(dsn: str) -> None:
    """get_tuple orders by checkpoint_id, which each writer mints from its own clock."""
    thread = new_thread()
    root = checkpoint(0, {"n": 0}, {"n": "1"})
    early = checkpoint(1, {"n": "minted first"}, {"n": "2"})
    late = checkpoint(1, {"n": "minted second"}, {"n": "2"})
    assert late["id"] > early["id"]
    with PostgresSaver.from_conn_string(dsn) as a, PostgresSaver.from_conn_string(dsn) as b:
        parent = a.put(config(thread), root, metadata(0), {"n": "1"})
        b.put(parent, late, metadata(1), {"n": "2"})
        a.put(parent, early, metadata(1), {"n": "2"})
        latest = a.get_tuple(config(thread))
    committed = dict(
        rows(
            dsn,
            "SELECT checkpoint_id, pg_xact_commit_timestamp(xmin) FROM checkpoints "
            "WHERE thread_id = %s",
            thread,
        )
    )
    assert committed[early["id"]] > committed[late["id"]]
    assert latest is not None and latest.checkpoint["id"] == late["id"]


@pytest.mark.parametrize("mode", PUTTERS)
def test_put_writes_blobs_and_checkpoint_in_one_transaction(dsn: str, mode: str) -> None:
    """The blob upserts and the checkpoint upsert share one pipeline, so one transaction."""
    thread = new_thread()
    versions: ChannelVersions = {"items": "1", "tags": "1"}
    cp = checkpoint(0, {"items": [1, 2, 3], "tags": {"a": 1}}, versions)
    PUTTERS[mode](dsn, config(thread), cp, metadata(0), versions)
    xids = rows(
        dsn,
        "SELECT xmin::text FROM checkpoints WHERE thread_id = %s "
        "UNION ALL SELECT xmin::text FROM checkpoint_blobs WHERE thread_id = %s",
        thread,
        thread,
    )
    assert len(xids) == 3
    assert len(set(xids)) == 1


@pytest.mark.parametrize("mode", PUTTERS)
def test_rejected_checkpoint_row_rolls_back_its_blobs(dsn: str, mode: str) -> None:
    """Postgres rejects a NUL character in JSONB; the blob rows sent before it roll back too.

    The test expects exactly that rejection (SQLSTATE 22P05), so a dropped connection or any other
    error cannot pass it.
    """
    thread = new_thread()
    versions: ChannelVersions = {"items": "1", "note": "1"}
    cp = checkpoint(0, {"items": [1, 2, 3], "note": "nul\x00byte"}, versions)
    with pytest.raises(psycopg.errors.UntranslatableCharacter):
        PUTTERS[mode](dsn, config(thread), cp, metadata(0), versions)
    assert rows(dsn, "SELECT count(*) FROM checkpoint_blobs WHERE thread_id = %s", thread) == [(0,)]
    assert rows(dsn, "SELECT count(*) FROM checkpoints WHERE thread_id = %s", thread) == [(0,)]


def test_put_writes_conflict_rules(dsn: str) -> None:
    """Regular writes keep the first writer's value; an all-special batch keeps the last one."""
    thread = new_thread()
    with PostgresSaver.from_conn_string(dsn) as a, PostgresSaver.from_conn_string(dsn) as b:
        saved = a.put(config(thread), checkpoint(0, {"n": 0}, {"n": "1"}), metadata(0), {"n": "1"})
        a.put_writes(saved, [("out", "from a")], "task-regular")
        b.put_writes(saved, [("out", "from b")], "task-regular")
        a.put_writes(saved, [(RESUME, "approve")], "task-special")
        b.put_writes(saved, [(RESUME, "reject")], "task-special")
        # A batch mixing regular and special channels uses the insert-only statement throughout.
        a.put_writes(saved, [("out", 1), (RESUME, "first")], "task-mixed")
        b.put_writes(saved, [("out", 2), (RESUME, "second")], "task-mixed")
    assert stored_writes(dsn, thread) == {
        ("task-regular", "out"): "from a",
        ("task-special", RESUME): "reject",
        ("task-mixed", "out"): 1,
        ("task-mixed", RESUME): "first",
    }
