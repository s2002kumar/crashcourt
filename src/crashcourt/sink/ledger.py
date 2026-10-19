"""The effect ledger: one SQLite WAL file per trial, append-only and hash-chained.

Each row records: sink_seq, received_mono_ns, effect_key, logical_op, op_id, attempt, worker, pid,
thread_id, node, checkpoint_id_seen, payload_hash, status. Each row also stores the
hash of the previous row and its own hash over its fields, so editing, deleting, or reordering
any row breaks verification from that row on.

Attempt counters live in the same file: a worker that is killed and replaced cannot reset them.

Idempotent effects (the N8 mitigation study's provider-side idempotency key). A ledger opened with
`apply_once` applies at most one effect per logical op: the first request for a logical op is
appended with status `applied`, every later one with status `duplicate`, decided and committed in
the same exclusive transaction as the row, with a unique partial index over the applied rows as
the backstop. Every attempt is still a row in the chain, so the evidence of what each worker
requested is complete, and the status is hashed with the rest. A ledger opened without it is the
file it always was: the same schema, meta, and rows.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from crashcourt.history.writer import RunIdentity, host_monotonic_ns
from crashcourt.spec.events import SCHEMA_VERSION, Event

#: 2: logical_op, pid, thread_id, and node are required (NOT NULL); version 1 allowed nulls.
LEDGER_VERSION = 2
GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS effects (
    sink_seq INTEGER PRIMARY KEY,
    received_mono_ns INTEGER NOT NULL,
    received_wall_ns INTEGER NOT NULL,
    effect_key TEXT NOT NULL,
    logical_op TEXT NOT NULL,
    op_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    worker TEXT NOT NULL,
    pid INTEGER NOT NULL,
    thread_id TEXT NOT NULL,
    node TEXT NOT NULL,
    checkpoint_id_seen TEXT,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""

#: Only in a ledger opened with apply_once: at most one applied row per logical op.
APPLY_ONCE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS effects_applied_once ON effects (logical_op)
    WHERE status = 'applied'
"""

#: The row fields covered by row_hash, in order.
HASHED_FIELDS: tuple[str, ...] = (
    "sink_seq",
    "received_mono_ns",
    "received_wall_ns",
    "effect_key",
    "logical_op",
    "op_id",
    "attempt",
    "worker",
    "pid",
    "thread_id",
    "node",
    "checkpoint_id_seen",
    "payload_hash",
    "status",
)


class EffectRequest(BaseModel):
    """What a worker sends for one effect. Only the checkpoint the worker saw may be unknown."""

    model_config = ConfigDict(extra="forbid")

    effect_key: str = Field(min_length=1)
    logical_op: str = Field(min_length=1)
    op_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    worker: str = Field(min_length=1)
    pid: int = Field(ge=1)
    thread_id: str = Field(min_length=1)
    node: str = Field(min_length=1)
    checkpoint_id_seen: str | None = None
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class LedgerConflict(ValueError):
    """The file belongs to another trial, or was written by another ledger version."""


@dataclass(frozen=True)
class TrialId:
    batch: str
    cell: str
    trial: int
    seed: str

    def path(self, root: Path) -> Path:
        safe = [part.replace("/", "_").replace("\\", "_") for part in (self.batch, self.cell)]
        return root / safe[0] / safe[1] / f"trial-{self.trial:05d}.sqlite"


@dataclass(frozen=True)
class Verification:
    ok: bool
    rows: int
    #: sink_seq of the first row whose chain does not hold, if any.
    first_bad: int | None


def row_hash(prev_hash: str, row: dict[str, Any]) -> str:
    body = json.dumps([row[name] for name in HASHED_FIELDS], separators=(",", ":"))
    return hashlib.sha256(f"{prev_hash}|{body}".encode()).hexdigest()


class Ledger:
    """One trial's ledger. Thread-safe; every write is committed before the method returns."""

    def __init__(self, path: Path, trial: TrialId, *, apply_once: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.trial = trial
        #: Apply at most one effect per logical op; the rest are recorded as duplicates.
        self.apply_once = apply_once
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        try:
            self._prepare(path, trial)
        except BaseException:
            self._conn.close()
            raise

    def _prepare(self, path: Path, trial: TrialId) -> None:
        # SQLite answers with the mode it is in; a file that cannot use WAL keeps another one.
        mode = str(self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if mode != "wal":
            raise ValueError(f"{path}: journal_mode is {mode}, not wal")
        self._conn.execute("PRAGMA synchronous=FULL")
        wanted = {
            "ledger_version": str(LEDGER_VERSION),
            **{k: str(v) for k, v in asdict(trial).items()},
            # Only an idempotent ledger records its mode, so a plain one keeps its meta.
            **({"apply_once": "1"} if self.apply_once else {}),
        }
        # Read and write meta in one transaction, so a crash cannot leave it half written and
        # two openers cannot both decide it is empty. The tables are created only for a new file:
        # an existing one keeps the schema of the version that wrote it, which the check refuses.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
            ).fetchone()
            meta = (
                dict(self._conn.execute("SELECT key, value FROM meta").fetchall()) if exists else {}
            )
            if not meta:
                for statement in filter(str.strip, SCHEMA.split(";")):
                    self._conn.execute(statement)
                if self.apply_once:
                    self._conn.execute(APPLY_ONCE_INDEX)
                self._conn.executemany("INSERT INTO meta VALUES (?, ?)", wanted.items())
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        if meta and meta.get("ledger_version") != wanted["ledger_version"]:
            raise LedgerConflict(
                f"{path} was written by ledger version {meta.get('ledger_version')}; "
                f"this sink writes version {LEDGER_VERSION}"
            )
        if meta and meta != wanted:
            if {k: v for k, v in meta.items() if k != "apply_once"} == {
                k: v for k, v in wanted.items() if k != "apply_once"
            }:
                raise LedgerConflict(
                    f"{path} was opened {'with' if 'apply_once' in meta else 'without'} "
                    f"apply_once; this opener asks for the opposite"
                )
            raise LedgerConflict(f"{path} belongs to another trial: {meta}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def append(self, effect: EffectRequest) -> dict[str, Any]:
        """Record one effect: every call is a new row, committed on return. With apply_once, a
        logical op that already has an applied row gets a `duplicate` row instead, decided in
        the same exclusive transaction; the answer says which (`applied`, `duplicate_of`)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                last = self._conn.execute(
                    "SELECT sink_seq, row_hash FROM effects ORDER BY sink_seq DESC LIMIT 1"
                ).fetchone()
                seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
                duplicate_of: int | None = None
                if self.apply_once:
                    first = self._conn.execute(
                        "SELECT sink_seq FROM effects WHERE logical_op = ? AND status = 'applied' "
                        "ORDER BY sink_seq LIMIT 1",
                        (effect.logical_op,),
                    ).fetchone()
                    duplicate_of = int(first[0]) if first else None
                row: dict[str, Any] = {
                    "sink_seq": seq,
                    "received_mono_ns": host_monotonic_ns(),
                    "received_wall_ns": time.time_ns(),
                    **effect.model_dump(),
                    "status": "applied" if duplicate_of is None else "duplicate",
                }
                digest = row_hash(prev, row)
                columns = [*HASHED_FIELDS, "prev_hash", "row_hash"]
                values = [row[name] for name in HASHED_FIELDS] + [prev, digest]
                self._conn.execute(
                    f"INSERT INTO effects ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    values,
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return {
            **row,
            "prev_hash": prev,
            "row_hash": digest,
            "applied": duplicate_of is None,
            "duplicate_of": duplicate_of,
        }

    def next_attempt(self, key: str) -> int:
        """Increment and return the counter for `key` (1 on first use), committed on return."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                value = self._conn.execute(
                    "INSERT INTO attempts (key, value) VALUES (?, 1) "
                    "ON CONFLICT (key) DO UPDATE SET value = value + 1 RETURNING value",
                    (key,),
                ).fetchone()[0]
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return int(value)

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM effects ORDER BY sink_seq")
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]

    def verify(self) -> Verification:
        rows = self.rows()
        prev = GENESIS
        for expected_seq, row in enumerate(rows, start=1):
            if (
                row["sink_seq"] != expected_seq
                or row["prev_hash"] != prev
                or row["row_hash"] != row_hash(prev, row)
            ):
                return Verification(False, len(rows), row["sink_seq"])
            prev = row["row_hash"]
        return Verification(True, len(rows), None)


def ledger_events(rows: list[dict[str, Any]], run: RunIdentity, pid: int) -> list[Event]:
    """The ledger as the sink's history: one `sink.append` per row, in sink_seq order.

    The ledger is the sink's record, so its history is derived from it rather than written
    separately; seq is sink_seq - 1 and mono_ns is when the sink received the effect.
    """
    return [
        Event(
            v=SCHEMA_VERSION,
            batch=run.batch,
            cell=run.cell,
            trial=run.trial,
            seed=run.seed,
            proc="sink",
            pid=pid,
            seq=int(row["sink_seq"]) - 1,
            mono_ns=int(row["received_mono_ns"]),
            wall_ns=int(row["received_wall_ns"]),
            kind="sink.append",
            op_id=row["op_id"],
            logical_op=row["logical_op"],
            attempt=int(row["attempt"]),
            effect_key=row["effect_key"],
            thread_id=row["thread_id"],
            checkpoint_id=row["checkpoint_id_seen"],
            status="ok",
            data={
                "worker": row["worker"],
                "sink_seq": int(row["sink_seq"]),
                "row_hash": row["row_hash"],
                # Only a row that was not applied names its status, so plain ledgers' histories
                # keep their form.
                **({"status": row["status"]} if row["status"] != "applied" else {}),
            },
        )
        for row in sorted(rows, key=lambda r: int(r["sink_seq"]))
    ]
