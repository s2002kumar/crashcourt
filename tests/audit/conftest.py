from __future__ import annotations

import os

import pytest

from crashcourt.probes import DEFAULT_PG_DSN


@pytest.fixture(scope="session")
def dsn() -> str:
    """The compose Postgres, reached directly (never through Toxiproxy); tables created once."""
    from langgraph.checkpoint.postgres import PostgresSaver

    value = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
    with PostgresSaver.from_conn_string(value) as saver:
        saver.setup()
    return value
