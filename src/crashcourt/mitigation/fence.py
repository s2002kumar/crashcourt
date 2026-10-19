"""A storage-checked ownership fence for one thread's checkpoint tables.

The problem it answers is N8's: a worker that still holds a thread's run after the op was handed
to another worker writes its checkpoint and task writes later, and the saver accepts them. The
fence makes PostgreSQL reject them inside the write's own transaction:

- `crashcourt_thread_owner` holds each thread's ownership epoch. The orchestrator issues epochs
  per thread, strictly increasing: 1 for the thread's first dispatch, one more for every later
  dispatch or redelivery (`Run.epoch`, recorded in `dispatch.send` and `dispatch.redeliver`).
- A worker claims the thread before it starts the run: one committed statement that raises the
  owner row's epoch to the run's, and does nothing if a higher epoch already holds it (the run is
  stale before it starts, and the worker fails it without reading anything).
- Every session binds its run's epoch (`crashcourt.epoch`, a session setting); the worker's pool
  binds it on each checkout, so every connection a run uses carries it.
- A BEFORE INSERT trigger on `checkpoints` and `checkpoint_writes` reads the owner row FOR SHARE
  and raises unless the session's epoch equals the owner's. BEFORE INSERT fires before ON CONFLICT
  is resolved, so a stale insert that would have been a silent `DO NOTHING` is rejected too. The
  share lock holds until the writing transaction ends, so a claim (which updates the row) waits
  for an in-flight write of the previous owner to commit or abort, and no write validates against
  an epoch that is replaced before it commits.
- The placebo installs the same table, claims, bindings, and trigger; the trigger decides and
  raises a NOTICE instead of an error, so it rejects nothing (the disarmed fence).

The saver is unchanged: the fence is schema the runner installs for a batch whose scenario asks
for it (`variant.fence`) and removes otherwise. Its limits are the design's: the epoch is a
session setting a trusted worker binds, not a credential; a worker that binds another run's epoch
defeats it. A fencing failure is observable as `saver.put.raise` or `saver.put_writes.raise` whose
error carries `FENCE_PREFIX`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

FenceMode = Literal["enforce", "placebo"]
#: The thread's current owner row.
OWNER_TABLE = "crashcourt_thread_owner"
#: The session setting that carries a run's epoch.
EPOCH_SETTING = "crashcourt.epoch"
#: Every fence error's message carries this; the evaluator and the tests match on it.
FENCE_PREFIX = "crashcourt fence:"
#: The file a trial directory holds for the owner rows of its threads after the trial.
OWNERS_FILE = "fence.json"
FENCED_TABLES = ("checkpoints", "checkpoint_writes")

#: The run's epoch in this task and the tasks it starts: the worker sets it before the run and
#: the pool's check callback binds it on every checkout.
run_epoch: ContextVar[int | None] = ContextVar("crashcourt_run_epoch", default=None)

_FUNCTION = """
CREATE OR REPLACE FUNCTION crashcourt_fence() RETURNS trigger LANGUAGE plpgsql AS $fence$
DECLARE
    mode TEXT := TG_ARGV[0];
    bound TEXT := current_setting('crashcourt.epoch', true);
    owner BIGINT;
    why TEXT;
BEGIN
    SELECT epoch INTO owner FROM crashcourt_thread_owner
        WHERE thread_id = NEW.thread_id FOR SHARE;
    IF bound IS NULL OR bound = '' THEN
        why := format('no epoch is bound to this session (thread %s, %s)',
                      NEW.thread_id, TG_TABLE_NAME);
    ELSIF owner IS NULL THEN
        why := format('thread %s has no owner (session epoch %s, %s)',
                      NEW.thread_id, bound, TG_TABLE_NAME);
    ELSIF bound::BIGINT <> owner THEN
        why := format('stale epoch %s for thread %s (owner epoch %s, %s)',
                      bound, NEW.thread_id, owner, TG_TABLE_NAME);
    END IF;
    IF why IS NOT NULL THEN
        IF mode = 'enforce' THEN
            RAISE EXCEPTION 'crashcourt fence: %', why;
        END IF;
        RAISE NOTICE 'crashcourt fence (placebo): %', why;
    END IF;
    RETURN NEW;
END
$fence$
"""

_TABLE = f"""
CREATE TABLE IF NOT EXISTS {OWNER_TABLE} (
    thread_id TEXT PRIMARY KEY,
    epoch BIGINT NOT NULL CHECK (epoch >= 1)
)
"""

#: Raise the owner's epoch to the run's; no row comes back when a higher epoch holds the thread.
CLAIM_SQL = f"""
INSERT INTO {OWNER_TABLE} (thread_id, epoch) VALUES (%s, %s)
ON CONFLICT (thread_id) DO UPDATE SET epoch = EXCLUDED.epoch
    WHERE {OWNER_TABLE}.epoch <= EXCLUDED.epoch
RETURNING epoch
"""


class FenceError(Exception):
    """The run's claim was refused: a higher epoch already owns the thread."""


def _trigger_name(table: str) -> str:
    return f"crashcourt_fence_{table}"


def install_statements(mode: FenceMode) -> list[sql.SQL | sql.Composed]:
    """The statements that install the fence in `mode`, in order; each is idempotent."""
    statements: list[sql.SQL | sql.Composed] = [sql.SQL(_TABLE), sql.SQL(_FUNCTION)]
    for table in FENCED_TABLES:
        statements.append(
            sql.SQL(
                "CREATE OR REPLACE TRIGGER {trigger} BEFORE INSERT ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION crashcourt_fence({mode})"
            ).format(
                trigger=sql.Identifier(_trigger_name(table)),
                table=sql.Identifier(table),
                mode=sql.Literal(mode),
            )
        )
    return statements


def remove_statements() -> list[sql.SQL | sql.Composed]:
    """The statements that leave the database as the saver's setup made it."""
    statements: list[sql.SQL | sql.Composed] = [
        sql.SQL("DROP TRIGGER IF EXISTS {trigger} ON {table}").format(
            trigger=sql.Identifier(_trigger_name(table)), table=sql.Identifier(table)
        )
        for table in FENCED_TABLES
    ]
    statements.append(sql.SQL("DROP FUNCTION IF EXISTS crashcourt_fence()"))
    statements.append(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(OWNER_TABLE)))
    return statements


def install(dsn: str, mode: FenceMode) -> None:
    """Install the fence on the saver's tables (after `setup()`), replacing any mode installed
    before; each statement commits on its own."""
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        for statement in install_statements(mode):
            conn.execute(statement)


def remove(dsn: str) -> None:
    """Remove the fence if any mode of it is installed; a no-op otherwise."""
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        for statement in remove_statements():
            conn.execute(statement)


def installed(dsn: str) -> FenceMode | None:
    """The mode the fence is installed in, or None when its triggers are absent."""
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        rows = conn.execute(
            "SELECT tgargs FROM pg_trigger WHERE tgname = ANY(%s) AND NOT tgisinternal",
            ([_trigger_name(t) for t in FENCED_TABLES],),
        ).fetchall()
    modes = {bytes(row[0]).rstrip(b"\0").decode("ascii") for row in rows}
    if not modes:
        return None
    if modes == {"enforce"} and len(rows) == len(FENCED_TABLES):
        return "enforce"
    if modes == {"placebo"} and len(rows) == len(FENCED_TABLES):
        return "placebo"
    raise RuntimeError(f"the fence is partly installed or names an unknown mode: {rows!r}")


async def bind_session(conn: psycopg.AsyncConnection[Any]) -> None:
    """Bind the current run's epoch to the connection, as the pool's `check` callback on every
    checkout; a run without an epoch (an unfenced batch) binds nothing and costs no round trip."""
    epoch = run_epoch.get()
    if epoch is not None:
        await conn.execute("SELECT set_config(%s, %s, false)", (EPOCH_SETTING, str(epoch)))


async def claim(conn: psycopg.AsyncConnection[Any], thread_id: str, epoch: int) -> None:
    """Take the thread for `epoch`, committed before this returns (the connection is in
    autocommit mode); FenceError when a higher epoch already owns it."""
    if not conn.autocommit:
        raise ValueError("a claim must commit on its own: the connection is not in autocommit")
    async with conn.cursor(row_factory=tuple_row) as cur:
        await cur.execute(CLAIM_SQL, (thread_id, epoch))
        row = await cur.fetchone()
    if row is None:
        async with conn.cursor(row_factory=tuple_row) as cur:
            await cur.execute(
                sql.SQL("SELECT epoch FROM {} WHERE thread_id = %s").format(
                    sql.Identifier(OWNER_TABLE)
                ),
                (thread_id,),
            )
            holder = await cur.fetchone()
        raise FenceError(
            f"{FENCE_PREFIX} epoch {epoch} for thread {thread_id} is stale before it starts "
            f"(owner epoch {holder[0] if holder else 'unknown'})"
        )


def is_fence_error(error: str | None) -> bool:
    """Whether a recorded error text is the fence's (`<ExceptionClass>: crashcourt fence: ...`)."""
    return error is not None and FENCE_PREFIX in error


def owners(dsn: str, threads: Sequence[str]) -> dict[str, int]:
    """The owner epoch of each thread that has one, read directly from the database."""
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        rows = conn.execute(
            sql.SQL("SELECT thread_id, epoch FROM {} WHERE thread_id = ANY(%s)").format(
                sql.Identifier(OWNER_TABLE)
            ),
            (list(threads),),
        ).fetchall()
    return {str(thread): int(epoch) for thread, epoch in rows}


def write_owners(directory: Path, dsn: str, threads: Sequence[str], mode: FenceMode) -> None:
    """Record the owner rows of the trial's threads after the trial (fence.json): the fence's
    own evidence of who owned each thread at the end, beside the snapshot."""
    record = {"v": 1, "mode": mode, "owners": owners(dsn, threads)}
    path = directory / OWNERS_FILE
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
