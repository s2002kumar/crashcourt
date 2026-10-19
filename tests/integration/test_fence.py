"""The ownership fence on PostgreSQL, proved narrowly before any N8-shaped run.

With the unchanged AsyncPostgresSaver on two sessions of a fresh database: the stale owner's
checkpoint put and task writes are rejected inside their own transactions and leave no row; a
stale task write that ON CONFLICT would have ignored is rejected too; a claim waits for an
in-flight write of the previous owner and no write validates against a replaced epoch; a session
with no epoch, or a thread with no owner, cannot write; the placebo decides and rejects nothing;
a database the fence was removed from behaves as the saver's setup made it; and the worker's
pool binds each checkout to its run's epoch. Then a single-writer batch under the fence, end to
end, with nothing rejected.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from crashcourt.history.reader import read_history
from crashcourt.mitigation import fence
from crashcourt.orchestrator.runner import RunOptions, Settings, run_batch
from crashcourt.probes import DEFAULT_PG_DSN
from crashcourt.spec.scenario import load_scenario

pytestmark = pytest.mark.integration

DSN = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
REPO = Path(__file__).resolve().parents[2]
THREAD = "fence/t1"


@contextmanager
def fresh_database() -> Iterator[str]:
    """A new, empty database with the saver's tables, dropped afterwards."""
    name = f"fence_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = make_conninfo(DSN, dbname=name)
    try:

        async def setup() -> None:
            async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
                await saver.setup()

        asyncio.run(setup())
        yield dsn
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@asynccontextmanager
async def session(
    dsn: str, epoch: int | None, *, autocommit: bool = True
) -> AsyncIterator[tuple[psycopg.AsyncConnection[Any], AsyncPostgresSaver]]:
    """One worker session: the saver on one connection configured as the worker's are
    (autocommit, no prepared statements, dict rows), bound to `epoch` as the worker's pool binds
    each checkout."""
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        conn = cast("psycopg.AsyncConnection[Any]", saver.conn)  # from_conn_string opens one
        if epoch is not None:
            await conn.execute(
                "SELECT set_config(%s, %s, false)", (fence.EPOCH_SETTING, str(epoch))
            )
        if not autocommit:
            await conn.set_autocommit(False)
        yield conn, saver


async def claim(dsn: str, epoch: int, thread: str = THREAD) -> None:
    """A run's claim: its own committed statement, as the worker makes it before the run."""
    async with session(dsn, epoch) as (conn, _):
        await fence.claim(conn, thread, epoch)


def checkpoint(step: int) -> Any:
    cp = empty_checkpoint()
    cp["channel_values"] = {"log": [f"s{i}" for i in range(step + 1)]}  # a blob, not inline
    cp["channel_versions"] = {"log": str(step + 1)}
    return cp


def config(checkpoint_id: str | None = None, thread: str = THREAD) -> Any:
    configurable: dict[str, Any] = {"thread_id": thread, "checkpoint_ns": ""}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


async def put(saver: AsyncPostgresSaver, step: int, parent: Any = None) -> Any:
    cp = checkpoint(step)
    metadata: Any = {"source": "loop", "step": step, "parents": {}}
    return await saver.aput(parent or config(), cp, metadata, cp["channel_versions"])


def rows(dsn: str, table: str, thread: str = THREAD) -> list[dict[str, Any]]:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {} WHERE thread_id = %s ORDER BY 1, 2, 3").format(
                sql.Identifier(table)
            ),
            (thread,),
        )
        return cur.fetchall()


def checkpoint_ids(dsn: str) -> list[str]:
    return sorted(r["checkpoint_id"] for r in rows(dsn, "checkpoints"))


# -- the stale owner's writes --------------------------------------------------------------------


def test_a_stale_owners_checkpoint_is_rejected_and_leaves_no_row_not_even_its_blob() -> None:
    with fresh_database() as dsn:
        fence.install(dsn, "enforce")

        async def scenario() -> tuple[str, list[str], Any]:
            async with session(dsn, 1) as (_, a), session(dsn, 2) as (_, b):
                await claim(dsn, 1)
                first = await put(a, 0)  # A owns the thread: durable
                await claim(dsn, 2)  # B takes it over, committed before B reads anything
                with pytest.raises(psycopg.errors.RaiseException) as caught:
                    await put(a, 1, first)  # A's next checkpoint: stale
                after_a = checkpoint_ids(dsn)
                read_by_b = await b.aget_tuple(config())
                latest = await put(b, 1, first)  # B's checkpoint on the same parent: durable
                return str(caught.value), after_a, (read_by_b, latest)

        error, after_a, (read_by_b, latest) = asyncio.run(scenario())
        assert fence.is_fence_error(error)
        assert f"stale epoch 1 for thread {THREAD} (owner epoch 2, checkpoints)" in error
        # A's first checkpoint is the only one; its rejected put wrote no blob either, since the
        # saver's blobs and checkpoint go in one implicit transaction that the error aborted.
        assert len(after_a) == 1
        assert sorted(r["version"] for r in rows(dsn, "checkpoint_blobs")) == ["1", "2"]
        assert (
            read_by_b is not None
            and read_by_b.config["configurable"]["checkpoint_id"] == after_a[0]
        )
        assert latest["configurable"]["checkpoint_id"] in checkpoint_ids(dsn)
        assert len(checkpoint_ids(dsn)) == 2
        assert fence.owners(dsn, [THREAD]) == {THREAD: 2}


def test_a_stale_task_write_is_rejected_even_where_on_conflict_would_have_ignored_it() -> None:
    with fresh_database() as dsn:
        fence.install(dsn, "enforce")

        async def scenario() -> list[str]:
            errors: list[str] = []
            async with session(dsn, 1) as (_, a), session(dsn, 2) as (_, b):
                await claim(dsn, 1)
                first = await put(a, 0)
                await claim(dsn, 2)
                await b.aput_writes(first, [("log", ["b"])], "task-x", "~path")
                # The same row B wrote: without the fence a silent DO NOTHING.
                for writes, task in (
                    ([("log", ["a"])], "task-x"),
                    ([("log", ["a"])], "task-y"),
                    ([("__error__", "boom")], "task-x"),  # the DO UPDATE path
                ):
                    with pytest.raises(psycopg.errors.RaiseException) as caught:
                        await a.aput_writes(first, writes, task, "~path")
                    errors.append(str(caught.value))
            return errors

        errors = asyncio.run(scenario())
        assert all(fence.is_fence_error(e) and "checkpoint_writes" in e for e in errors)
        written = rows(dsn, "checkpoint_writes")
        assert [(r["task_id"], r["channel"]) for r in written] == [("task-x", "log")]


def test_a_claim_waits_for_the_previous_owners_open_write_and_fences_its_next_one() -> None:
    """A's write transaction holds the owner row FOR SHARE: B's claim blocks (the backend waits
    on a lock) until A commits; A's write committed under its own epoch stays, and A's next write
    sees the new owner and is rejected."""
    with fresh_database() as dsn:
        fence.install(dsn, "enforce")

        async def scenario() -> tuple[str, bool, str]:
            await claim(dsn, 1)
            async with session(dsn, 1) as (_, a_auto):
                first = await put(a_auto, 0)
            async with (
                session(dsn, 1, autocommit=False) as (a_conn, a),
                session(dsn, None) as (watch, _),
            ):
                await a.aput_writes(first, [("log", ["held"])], "task-a", "~path")  # open txn
                b_claim = asyncio.create_task(claim(dsn, 2))
                waited = ""
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    async with watch.cursor(row_factory=dict_row) as cur:
                        await cur.execute(
                            "SELECT wait_event_type, query FROM pg_stat_activity "
                            "WHERE query LIKE %s AND state = 'active'",
                            ("%INSERT INTO crashcourt_thread_owner%",),
                        )
                        found = await cur.fetchall()
                    if any(r["wait_event_type"] == "Lock" for r in found):
                        waited = "Lock"
                        break
                blocked = not b_claim.done()
                await a_conn.commit()
                await asyncio.wait_for(b_claim, 10)
                with pytest.raises(psycopg.errors.RaiseException) as caught:
                    await a.aput_writes(first, [("log", ["late"])], "task-late", "~path")
                await a_conn.rollback()
                return waited, blocked, str(caught.value)

        waited, blocked, error = asyncio.run(scenario())
        assert waited == "Lock" and blocked
        assert "stale epoch 1" in error and "owner epoch 2" in error
        assert [r["task_id"] for r in rows(dsn, "checkpoint_writes")] == ["task-a"]
        assert fence.owners(dsn, [THREAD]) == {THREAD: 2}


def test_a_session_with_no_epoch_or_a_thread_with_no_owner_cannot_write() -> None:
    with fresh_database() as dsn:
        fence.install(dsn, "enforce")

        async def scenario() -> list[str]:
            errors: list[str] = []
            async with session(dsn, 1) as (_, bound), session(dsn, None) as (_, unbound):
                with pytest.raises(psycopg.errors.RaiseException) as caught:
                    await put(bound, 0)  # nobody claimed the thread
                errors.append(str(caught.value))
                await claim(dsn, 1)
                with pytest.raises(psycopg.errors.RaiseException) as caught:
                    await put(unbound, 0)
                errors.append(str(caught.value))
                await put(bound, 0)
            return errors

        errors = asyncio.run(scenario())
        assert f"thread {THREAD} has no owner (session epoch 1, checkpoints)" in errors[0]
        assert "no epoch is bound to this session" in errors[1]
        assert len(checkpoint_ids(dsn)) == 1


def test_a_claim_below_the_owner_is_refused_and_the_same_epoch_again_is_not() -> None:
    with fresh_database() as dsn:
        fence.install(dsn, "enforce")

        async def scenario() -> str:
            await claim(dsn, 2)
            await claim(dsn, 2)
            with pytest.raises(fence.FenceError) as caught:
                await claim(dsn, 1)
            return str(caught.value)

        error = asyncio.run(scenario())
        assert error == (
            f"crashcourt fence: epoch 1 for thread {THREAD} is stale before it starts "
            "(owner epoch 2)"
        )
        assert fence.owners(dsn, [THREAD]) == {THREAD: 2}


# -- the placebo and the removal ------------------------------------------------------------------


def test_the_placebo_decides_the_same_and_rejects_nothing() -> None:
    with fresh_database() as dsn:
        fence.install(dsn, "placebo")
        assert fence.installed(dsn) == "placebo"

        async def scenario() -> list[str]:
            notices: list[str] = []
            async with session(dsn, 1) as (a_conn, a), session(dsn, 2) as (_, b):
                a_conn.add_notice_handler(lambda d: notices.append(str(d.message_primary)))
                await claim(dsn, 1)
                first = await put(a, 0)
                await claim(dsn, 2)
                await put(a, 1, first)  # stale, and accepted
                await a.aput_writes(first, [("log", ["a"])], "task-a", "~path")
                await put(b, 1, first)
            return notices

        notices = asyncio.run(scenario())
        assert len(checkpoint_ids(dsn)) == 3
        assert [r["task_id"] for r in rows(dsn, "checkpoint_writes")] == ["task-a"]
        assert notices == [
            f"crashcourt fence (placebo): stale epoch 1 for thread {THREAD} "
            "(owner epoch 2, checkpoints)",
            f"crashcourt fence (placebo): stale epoch 1 for thread {THREAD} "
            "(owner epoch 2, checkpoint_writes)",
        ]


def test_removing_the_fence_leaves_the_database_as_setup_made_it() -> None:
    with fresh_database() as dsn:
        assert fence.installed(dsn) is None
        fence.install(dsn, "enforce")
        assert fence.installed(dsn) == "enforce"
        fence.install(dsn, "placebo")  # a batch in the other mode replaces it
        assert fence.installed(dsn) == "placebo"
        fence.remove(dsn)
        fence.remove(dsn)  # and removing twice is nothing
        assert fence.installed(dsn) is None
        with psycopg.connect(dsn, autocommit=True) as conn:
            assert conn.execute("SELECT to_regclass('crashcourt_thread_owner')").fetchone() == (
                None,
            )
            assert conn.execute(
                "SELECT count(*) FROM pg_proc WHERE proname = 'crashcourt_fence'"
            ).fetchone() == (0,)

        async def scenario() -> None:
            async with session(dsn, None) as (_, unbound), session(dsn, 1) as (_, stale):
                first = await put(unbound, 0)
                await put(stale, 1, first)
                await stale.aput_writes(first, [("log", ["x"])], "task", "~path")

        asyncio.run(scenario())
        assert len(checkpoint_ids(dsn)) == 2


def test_the_pool_binds_each_checkout_to_its_runs_epoch() -> None:
    with fresh_database() as dsn:

        async def scenario() -> list[tuple[int | None, str | None]]:
            async with AsyncConnectionPool(
                dsn,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                min_size=1,
                max_size=2,
                check=fence.bind_session,
            ) as pool:

                async def run(epoch: int | None) -> tuple[int | None, str | None]:
                    if epoch is not None:
                        fence.run_epoch.set(epoch)
                    seen: set[str | None] = set()
                    for _ in range(3):
                        async with pool.connection() as conn:
                            cur = await conn.execute(
                                "SELECT current_setting(%s, true) AS epoch", (fence.EPOCH_SETTING,)
                            )
                            row = await cur.fetchone()
                            seen.add(row["epoch"] if row else None)
                            await asyncio.sleep(0.01)
                    assert len(seen) == 1, seen
                    return epoch, seen.pop()

                return list(await asyncio.gather(run(1), run(2), run(1), run(2)))

        results = asyncio.run(scenario())
        assert results == [(1, "1"), (2, "2"), (1, "1"), (2, "2")]


# -- a single writer under the fence, end to end ---------------------------------------------------


@pytest.mark.skipif(sys.platform != "linux", reason="crashcourt run needs Linux; CI runs it")
def test_a_single_writer_batch_under_the_fence_claims_every_run_and_rejects_nothing(
    tmp_path: Path,
) -> None:
    path = Path(__file__).parent / "scenarios" / "fenced-sequential.yaml"
    scenario = load_scenario(path)
    assert scenario.fence == "enforce"
    summary = run_batch(
        scenario, RunOptions(trials=2, out=tmp_path), Settings.from_environment(REPO), source=path
    )
    assert [t.valid for t in summary.trials] == [True, True]
    run_record = json.loads((summary.directory / "run.json").read_text(encoding="utf-8"))
    assert run_record["fence"] == "enforce"
    for trial in summary.trials:
        record = json.loads((trial.directory / "trial.json").read_text(encoding="utf-8"))
        assert record["fence"] == "enforce" and record["client_outcomes"] == {"ok": 6}
        orchestrator = read_history(trial.directory / "orchestrator.jsonl").events
        sends = [(e.op_id, e.data["epoch"]) for e in orchestrator if e.kind == "dispatch.send"]
        assert sends == [
            ("op-1", 1),
            ("op-2", 2),
            ("op-3", 1),
            ("op-4", 3),
            ("op-5", 2),
            ("op-6", 3),
        ]
        prefix = f"{summary.batch}/{scenario.cell}/{trial.number}"
        owners = json.loads((trial.directory / fence.OWNERS_FILE).read_text(encoding="utf-8"))
        assert owners == {
            "v": 1,
            "mode": "enforce",
            "owners": {f"{prefix}/t1": 3, f"{prefix}/t2": 3},
        }
        for worker in ("w1", "w2"):
            events = read_history(trial.directory / f"{worker}.jsonl").events
            assert not [e for e in events if e.kind.endswith(".raise")]
            assert [e for e in events if e.kind == "saver.put.return"]
    # The next batch without a fence runs on a database the fence has left.
    assert fence.installed(Settings.from_environment(REPO).direct_dsn) == "enforce"
    plain = REPO / "scenarios" / "smoke.yaml"
    unfenced = run_batch(
        load_scenario(plain),
        RunOptions(trials=1, out=tmp_path / "plain"),
        Settings.from_environment(REPO),
        source=plain,
    )
    assert unfenced.trials[0].valid
    assert fence.installed(Settings.from_environment(REPO).direct_dsn) is None
