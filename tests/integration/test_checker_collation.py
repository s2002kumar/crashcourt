"""The checker's head is the largest checkpoint_id by Python string order; the saver's is
`ORDER BY checkpoint_id DESC` under the column's collation. This checks they agree on uuid6 ids
(red-team specification 2.0), on the saver's own table in the compose Postgres.

Usage: docker compose up -d --wait && uv run pytest -m integration
"""

from __future__ import annotations

import os
import random
import uuid

import psycopg
import pytest
from langgraph.checkpoint.base.id import uuid6
from langgraph.checkpoint.postgres import PostgresSaver

from crashcourt.probes import DEFAULT_PG_DSN

pytestmark = pytest.mark.integration


def _ids() -> list[str]:
    """uuid6 ids as the checkpointer makes them, many in the same millisecond, plus ids with every
    clock sequence and node pattern a sort could trip on."""
    rng = random.Random(6)
    made = [str(uuid6(clock_seq=rng.randrange(1 << 14))) for _ in range(2_000)]
    made += [str(uuid6(clock_seq=s)) for s in (0, 1, 0x3FFF)]
    return made


def test_python_order_matches_the_saver_columns_collation() -> None:
    dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()
    thread = f"collation-check-{uuid.uuid4()}"
    ids = _ids()
    with psycopg.connect(dsn, autocommit=True) as conn:
        collation = conn.execute(
            "SELECT collation_name FROM information_schema.columns "
            "WHERE table_name = 'checkpoints' AND column_name = 'checkpoint_id'"
        ).fetchone()
        datcollate = conn.execute(
            "SELECT datcollate FROM pg_database WHERE datname = current_database()"
        ).fetchone()
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, "
                    "metadata) VALUES (%s, '', %s, '{}'::jsonb, '{}'::jsonb)",
                    [(thread, i) for i in ids],
                )
            database = [
                row[0]
                for row in conn.execute(
                    "SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s AND "
                    "checkpoint_ns = '' ORDER BY checkpoint_id DESC",
                    (thread,),
                ).fetchall()
            ]
            head = conn.execute(
                "SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s AND checkpoint_ns = '' "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (thread,),
            ).fetchone()
        finally:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread,))
    assert database == sorted(ids, reverse=True), (collation, datcollate)
    assert head is not None and head[0] == max(ids)
