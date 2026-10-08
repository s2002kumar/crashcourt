"""Control C4 on the primary target: the conformance suite and the sync script through the
wrapper on Postgres, with the wrapper recording as in a trial."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from crashcourt.probes import DEFAULT_PG_DSN

from ..c4 import (
    fresh_postgres,
    fresh_sync_postgres,
    recorded,
    report,
    script_checkpoints,
    sync_reads,
    sync_script,
    writer,
)

pytestmark = pytest.mark.integration


DSN = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)


def test_async_postgres_conformance_is_identical_through_the_wrapper(tmp_path: Path) -> None:
    factory = fresh_postgres(DSN)

    async def both() -> tuple[dict[str, object], dict[str, object]]:
        raw = await report("raw", factory, history_dir=None)
        wrapped = await report("wrapped", factory, history_dir=tmp_path)
        return raw, wrapped

    raw, wrapped = asyncio.run(both(), loop_factory=asyncio.SelectorEventLoop)
    assert wrapped == raw
    assert list(tmp_path.glob("wrapped-*.jsonl")), "the wrapper recorded"


def test_sync_postgres_calls_return_and_store_the_same_through_the_wrapper(tmp_path: Path) -> None:
    checkpoints = script_checkpoints()
    with (
        fresh_sync_postgres(DSN) as raw,
        fresh_sync_postgres(DSN) as inner,
        writer(tmp_path / "worker.jsonl", "sync-postgres") as history,
    ):
        through_raw = sync_script(raw, checkpoints)
        assert sync_script(recorded(inner, history), checkpoints) == through_raw
        assert sync_reads(inner, through_raw[1]) == through_raw[-1]
