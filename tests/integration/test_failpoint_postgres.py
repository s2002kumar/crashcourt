"""Control C4 on the primary target: the conformance suite through the wrapper on Postgres."""

from __future__ import annotations

import asyncio
import os

import pytest

from crashcourt.probes import DEFAULT_PG_DSN

from ..c4 import fresh_postgres, report

pytestmark = pytest.mark.integration


def test_async_postgres_conformance_is_identical_through_the_wrapper() -> None:
    factory = fresh_postgres(os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN))

    async def both() -> tuple[dict[str, object], dict[str, object]]:
        raw = await report("raw", factory, wrapped=False)
        wrapped = await report("wrapped", factory, wrapped=True)
        return raw, wrapped

    raw, wrapped = asyncio.run(both(), loop_factory=asyncio.SelectorEventLoop)
    assert wrapped == raw
