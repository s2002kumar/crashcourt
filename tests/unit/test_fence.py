"""The ownership fence without a database: its statements, its error signature, the session
binding, and the claim's refusal. PostgreSQL's part is proved in tests/integration/test_fence.py."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from crashcourt.mitigation import fence


class FakeConnection:
    """Records what the binding executes."""

    def __init__(self, *, autocommit: bool = True) -> None:
        self.autocommit = autocommit
        self.executed: list[tuple[str, Any]] = []

    async def execute(self, query: str, params: Any = None) -> None:
        self.executed.append((query, params))


def test_install_names_the_mode_in_both_triggers_and_remove_undoes_it() -> None:
    for mode in ("enforce", "placebo"):
        statements = [s.as_string() for s in fence.install_statements(mode)]
        assert statements[0].startswith("\nCREATE TABLE IF NOT EXISTS crashcourt_thread_owner")
        assert "CREATE OR REPLACE FUNCTION crashcourt_fence()" in statements[1]
        assert statements[2:] == [
            f'CREATE OR REPLACE TRIGGER "crashcourt_fence_{table}" BEFORE INSERT ON "{table}" '
            f"FOR EACH ROW EXECUTE FUNCTION crashcourt_fence('{mode}')"
            for table in ("checkpoints", "checkpoint_writes")
        ]
    removed = [s.as_string() for s in fence.remove_statements()]
    assert removed == [
        'DROP TRIGGER IF EXISTS "crashcourt_fence_checkpoints" ON "checkpoints"',
        'DROP TRIGGER IF EXISTS "crashcourt_fence_checkpoint_writes" ON "checkpoint_writes"',
        "DROP FUNCTION IF EXISTS crashcourt_fence()",
        'DROP TABLE IF EXISTS "crashcourt_thread_owner"',
    ]


def test_the_trigger_reads_the_owner_for_share_and_decides_before_on_conflict() -> None:
    body = fence.install_statements("enforce")[1].as_string()
    assert "FOR SHARE" in body and "BEFORE INSERT" not in body  # the trigger kind is on the trigger
    assert "RAISE EXCEPTION 'crashcourt fence: %'" in body
    assert "RAISE NOTICE 'crashcourt fence (placebo): %'" in body
    assert "current_setting('crashcourt.epoch', true)" in body


def test_a_fence_error_is_recognized_by_its_prefix_in_any_recorded_error() -> None:
    assert fence.is_fence_error("RaiseException: crashcourt fence: stale epoch 1 for thread t")
    assert fence.is_fence_error("FenceError: crashcourt fence: epoch 1 for thread t is stale")
    assert not fence.is_fence_error("OperationalError: server closed the connection")
    assert not fence.is_fence_error(None)


def test_the_binding_sets_the_epoch_only_when_the_run_has_one() -> None:
    async def scenario() -> tuple[list[Any], list[Any]]:
        unfenced = FakeConnection()
        await fence.bind_session(unfenced)  # type: ignore[arg-type]

        async def fenced_run() -> list[Any]:
            fence.run_epoch.set(7)
            conn = FakeConnection()
            await fence.bind_session(conn)  # type: ignore[arg-type]
            return conn.executed

        # The epoch set in one task is not seen by its siblings, only by what it starts.
        fenced = await asyncio.create_task(fenced_run())
        after = FakeConnection()
        await fence.bind_session(after)  # type: ignore[arg-type]
        return unfenced.executed + after.executed, fenced

    none, bound = asyncio.run(scenario())
    assert none == []
    assert bound == [("SELECT set_config(%s, %s, false)", ("crashcourt.epoch", "7"))]


def test_a_claim_needs_a_connection_that_commits_on_its_own() -> None:
    with pytest.raises(ValueError, match="autocommit"):
        asyncio.run(fence.claim(FakeConnection(autocommit=False), "t", 1))  # type: ignore[arg-type]


def test_the_claim_raises_the_epoch_and_keeps_a_higher_one() -> None:
    assert "ON CONFLICT (thread_id) DO UPDATE SET epoch = EXCLUDED.epoch" in fence.CLAIM_SQL
    assert "WHERE crashcourt_thread_owner.epoch <= EXCLUDED.epoch" in fence.CLAIM_SQL
    assert fence.CLAIM_SQL.strip().endswith("RETURNING epoch")
