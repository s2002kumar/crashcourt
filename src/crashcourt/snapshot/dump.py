"""The direct post-trial snapshot: raw saver rows, never read through the saver.

The checker judges a trial from this dump (crashcourt.checker.artifacts.Snapshot), so it reads the
database as the oracle: on the direct DSN (never through a proxy), in one read-only, repeatable-
read transaction, the three saver tables' rows for the trial's threads, each with `xmin` and
`pg_xact_commit_timestamp(xmin)`, plus the durability settings and the postmaster's start time.
Values stay as the saver stored them (types and blob bytes); decoding is the checker's, under
strict msgpack.

`xmin` is the transaction that wrote the current version of a row, so an upsert hides the original
insert: commit times are "last write" times.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from crashcourt.checker.artifacts import (
    BlobRow,
    CheckpointRow,
    Snapshot,
    SnapshotMeta,
    SnapshotPair,
    WriteRow,
)

SETTINGS = ("fsync", "synchronous_commit", "track_commit_timestamp")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_CHECKPOINTS = """
SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata,
       xmin::text, pg_xact_commit_timestamp(xmin)
FROM checkpoints WHERE thread_id = ANY(%s)
ORDER BY thread_id, checkpoint_ns, checkpoint_id
"""
_BLOBS = """
SELECT thread_id, checkpoint_ns, channel, version, type, blob,
       xmin::text, pg_xact_commit_timestamp(xmin)
FROM checkpoint_blobs WHERE thread_id = ANY(%s)
ORDER BY thread_id, checkpoint_ns, channel, version
"""
_WRITES = """
SELECT thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob,
       xmin::text, pg_xact_commit_timestamp(xmin)
FROM checkpoint_writes WHERE thread_id = ANY(%s)
ORDER BY thread_id, checkpoint_ns, checkpoint_id, task_id, idx
"""
_COUNT = """
SELECT (SELECT count(*) FROM checkpoints WHERE thread_id = %(t)s)
     + (SELECT count(*) FROM checkpoint_blobs WHERE thread_id = %(t)s)
     + (SELECT count(*) FROM checkpoint_writes WHERE thread_id = %(t)s)
"""


def _ns(value: datetime | None) -> int | None:
    """A commit timestamp in nanoseconds since the epoch, exactly (Postgres keeps microseconds)."""
    if value is None:
        return None
    return (value - EPOCH) // timedelta(microseconds=1) * 1000


def redact(dsn: str) -> str:
    """The DSN as recorded: every parameter but the password."""
    params = {k: v for k, v in conninfo_to_dict(dsn).items() if k != "password"}
    return make_conninfo(**params)  # pyright: ignore[reportArgumentType]


def _show(conn: psycopg.Connection[Any], name: str) -> str:
    row: tuple[Any, ...] | None = conn.execute("SELECT current_setting(%s)", (name,)).fetchone()
    return "" if row is None else str(row[0])


def _connect(dsn: str) -> psycopg.Connection[Any]:
    return psycopg.connect(dsn, autocommit=True, options="-c default_transaction_read_only=on")


def take(
    dsn: str,
    threads: Sequence[str],
    *,
    after_orchestrator_seq: int,
    toxics_installed: Sequence[str] = (),
) -> Snapshot:
    """One snapshot of the trial's threads, read in a single repeatable-read transaction."""
    with _connect(dsn) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        ids = list(threads)
        checkpoints = [
            CheckpointRow(
                thread_id=r[0],
                checkpoint_ns=r[1],
                checkpoint_id=r[2],
                parent_checkpoint_id=r[3],
                checkpoint=r[4],
                metadata=r[5],
                xmin=r[6],
                commit_ns=_ns(r[7]),
            )
            for r in conn.execute(_CHECKPOINTS, (ids,)).fetchall()
        ]
        blobs = [
            BlobRow(
                thread_id=r[0],
                checkpoint_ns=r[1],
                channel=r[2],
                version=r[3],
                type=r[4],
                blob=bytes(r[5]) if r[5] is not None else None,
                xmin=r[6],
                commit_ns=_ns(r[7]),
            )
            for r in conn.execute(_BLOBS, (ids,)).fetchall()
        ]
        writes = [
            WriteRow(
                thread_id=r[0],
                checkpoint_ns=r[1],
                checkpoint_id=r[2],
                task_id=r[3],
                task_path=r[4],
                idx=r[5],
                channel=r[6],
                type=r[7],
                blob=bytes(r[8]) if r[8] is not None else None,
                xmin=r[9],
                commit_ns=_ns(r[10]),
            )
            for r in conn.execute(_WRITES, (ids,)).fetchall()
        ]
        settings = {name: _show(conn, name) for name in SETTINGS}
        started = conn.execute("SELECT pg_postmaster_start_time()").fetchone()
    assert started is not None
    meta = SnapshotMeta(
        after_orchestrator_seq=after_orchestrator_seq,
        dsn=redact(dsn),
        settings=settings,
        postmaster_start_ns=_ns(started[0]) or 0,
        toxics_installed=tuple(toxics_installed),
        threads=tuple(threads),
    )
    return Snapshot(
        meta=meta, checkpoints=tuple(checkpoints), blobs=tuple(blobs), writes=tuple(writes)
    )


def pretrial_rows(dsn: str, threads: Sequence[str]) -> dict[str, int]:
    """Rows already present for each thread: taken before the first dispatch, it must be zero."""
    with _connect(dsn) as conn:
        counts: dict[str, int] = {}
        for thread_id in threads:
            row = conn.execute(_COUNT, {"t": thread_id}).fetchone()
            counts[thread_id] = int(row[0]) if row is not None else -1
        return counts


def take_pair(
    dsn: str,
    threads: Sequence[str],
    pretrial: dict[str, int],
    *,
    after_orchestrator_seq: int,
    toxics_installed: Sequence[str] = (),
    gap_s: float = 2.0,
) -> SnapshotPair:
    """Two snapshots at least `gap_s` seconds apart after quiescence; the checker compares them."""
    first = take(
        dsn,
        threads,
        after_orchestrator_seq=after_orchestrator_seq,
        toxics_installed=toxics_installed,
    )
    time.sleep(gap_s)
    second = take(
        dsn,
        threads,
        after_orchestrator_seq=after_orchestrator_seq,
        toxics_installed=toxics_installed,
    )
    return SnapshotPair(first=first, second=second, pretrial_rows=dict(pretrial))
