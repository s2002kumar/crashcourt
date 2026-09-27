"""Control C4's conformance half: run the official suite on a raw and a wrapped saver.

`langgraph-checkpoint-conformance` is a dev dependency, so this helper lives with the tests; the
T-06 evidence script imports it too.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.conformance import checkpointer_test, validate
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.conninfo import make_conninfo

from crashcourt.failpoints.saver import wrap

SaverFactory = Callable[[], AbstractAsyncContextManager[BaseCheckpointSaver[Any]]]


async def report(name: str, factory: SaverFactory, *, wrapped: bool) -> dict[str, Any]:
    """The suite's report for a fresh saver per capability, optionally wrapped."""

    async def saver() -> AsyncIterator[BaseCheckpointSaver[Any]]:
        async with factory() as inner:
            yield wrap(inner) if wrapped else inner

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
