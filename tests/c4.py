"""Control C4: run the official suite, and a fixed sync script, on a raw and a wrapped saver.

The wrapped saver records the way a trial does: a history writer and an empty failpoint registry.
`langgraph-checkpoint-conformance` is installed with the harness, since its version is in every
manifest; this helper lives with the tests, and the T-06 evidence script imports it too.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Generator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, empty_checkpoint
from langgraph.checkpoint.conformance import checkpointer_test, validate
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.conninfo import make_conninfo

from crashcourt.failpoints.registry import FailpointRegistry
from crashcourt.failpoints.saver import FailpointSaver, wrap
from crashcourt.history.writer import HistoryWriter, RunIdentity

SaverFactory = Callable[[], AbstractAsyncContextManager[BaseCheckpointSaver[Any]]]


WORKER = "worker-1"


def recorded(inner: BaseCheckpointSaver[Any], history: HistoryWriter) -> FailpointSaver:
    """`inner` wrapped as a trial wraps it: recording, with an empty failpoint registry."""
    return wrap(inner, history=history, registry=FailpointRegistry(WORKER))


def writer(path: Path, cell: str) -> HistoryWriter:
    run = RunIdentity(batch="DEMO-c4", cell=f"DEMO-{cell}", trial=0, seed="0")
    return HistoryWriter(path, run=run, proc=WORKER)


async def report(name: str, factory: SaverFactory, *, history_dir: Path | None) -> dict[str, Any]:
    """The suite's report for a fresh saver per capability: raw, or recorded into history_dir
    (one history file per saver the suite creates) when history_dir is given."""
    numbers = itertools.count()

    async def saver() -> AsyncIterator[BaseCheckpointSaver[Any]]:
        async with factory() as inner:
            if history_dir is None:
                yield inner
                return
            path = history_dir / f"{name}-{next(numbers):03d}.jsonl"
            with writer(path, f"c4-{name}") as history:
                yield recorded(inner, history)

    registered = checkpointer_test(name=name)(saver)
    result: dict[str, Any] = (await validate(registered)).to_dict()
    result.pop("checkpointer_name", None)
    return result


def fresh_postgres(admin_dsn: str) -> SaverFactory:
    """An AsyncPostgresSaver on a new, empty database, dropped afterwards.

    The suite lists checkpoints across all threads, so it needs a database no one else writes.
    """

    @asynccontextmanager
    async def factory() -> AsyncGenerator[BaseCheckpointSaver[Any]]:
        name = f"c4_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            async with AsyncPostgresSaver.from_conn_string(
                make_conninfo(admin_dsn, dbname=name)
            ) as saver:
                await saver.setup()
                yield saver
        finally:
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )

    return factory


@contextmanager
def fresh_sync_postgres(admin_dsn: str) -> Generator[BaseCheckpointSaver[Any]]:
    """A PostgresSaver on a new, empty database, dropped afterwards."""
    name = f"c4_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with PostgresSaver.from_conn_string(make_conninfo(admin_dsn, dbname=name)) as saver:
            saver.setup()
            yield saver
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


THREAD: RunnableConfig = {"configurable": {"thread_id": "t-sync", "checkpoint_ns": ""}}


def script_checkpoints() -> list[Checkpoint]:
    """Three checkpoints for the sync script, made once so both savers store the same ones."""
    checkpoints: list[Checkpoint] = []
    for step in range(3):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"n": step, "log": [f"s{i}" for i in range(step + 1)]}
        checkpoint["channel_versions"] = {"n": str(step + 1), "log": str(step + 1)}
        checkpoints.append(checkpoint)
    return checkpoints


def sync_script(saver: BaseCheckpointSaver[Any], checkpoints: list[Checkpoint]) -> list[Any]:
    """The failpoint operations' sync methods (put, put_writes, get_tuple, list) in a fixed
    order; returns what each call returned."""
    returned: list[Any] = []
    config = THREAD
    for step, checkpoint in enumerate(checkpoints):
        metadata: Any = {"source": "loop", "step": step, "parents": {}}
        config = saver.put(config, checkpoint, metadata, dict(checkpoint["channel_versions"]))
        returned.append(config)
        saver.put_writes(config, [("n", step), ("log", f"w{step}")], f"task-{step}", "p")
    returned.append(sync_reads(saver, returned[1]))
    return returned


def sync_reads(saver: BaseCheckpointSaver[Any], middle: RunnableConfig) -> list[Any]:
    """What a reader sees: the latest checkpoint, one by id, and listings."""
    thread_only: RunnableConfig = {"configurable": {"thread_id": "t-sync"}}
    return [
        saver.get_tuple(THREAD),
        saver.get_tuple(middle),
        list(saver.list(thread_only)),
        list(saver.list(thread_only, limit=2)),
        list(saver.list(thread_only, before=middle)),
    ]


def assert_meaningful(returned: list[Any]) -> None:
    """Guard against two empty results comparing equal: the script stored and read back."""
    latest, middle, listed, limited, older = returned[-1]
    assert latest is not None and middle is not None
    assert len(latest.pending_writes or ()) == 2 and len(middle.pending_writes or ()) == 2
    assert [len(listed), len(limited), len(older)] == [3, 2, 1]
