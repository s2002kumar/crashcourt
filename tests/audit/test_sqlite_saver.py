"""Direct-saver audit of langgraph-checkpoint-sqlite 3.1.1 (T-03): characterization, one file.

Two SqliteSaver instances on separate connections to one database file stand in for two workers.
These record how the contrast implementation behaves; SQLite makes no multi-writer claim either.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.serde.types import RESUME
from langgraph.checkpoint.sqlite import SqliteSaver

from ._checkpoints import checkpoint, config, metadata, new_thread


def lineage(path: Path, thread_id: str) -> dict[str, str | None]:
    with sqlite3.connect(path) as conn:
        return dict(
            conn.execute(
                "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            ).fetchall()
        )


def stored_writes(path: Path, thread_id: str) -> dict[tuple[str, str], object]:
    """Pending writes as the database file holds them, decoded with the default serializer."""
    serde = JsonPlusSerializer()
    with sqlite3.connect(path) as conn:
        found = conn.execute(
            "SELECT task_id, channel, type, value FROM writes WHERE thread_id = ?", (thread_id,)
        ).fetchall()
    return {
        (task, channel): serde.loads_typed((kind, value)) for task, channel, kind, value in found
    }


def test_two_sqlite_savers_fork_one_parent(tmp_path: Path) -> None:
    """The fork langgraph#8796 and the xbstack repro (https://github.com/xbstack/langgraph-same-thread-concurrent-writer-repro)
    show on Postgres, recorded for SQLite: a replication on the contrast implementation."""
    path = tmp_path / "checkpoints.sqlite"
    thread = new_thread()
    root = checkpoint(0, {"n": 0}, {"n": "1"})
    child_a = checkpoint(1, {"n": 1}, {"n": "2"})
    child_b = checkpoint(1, {"n": 2}, {"n": "2"})
    with SqliteSaver.from_conn_string(str(path)) as a, SqliteSaver.from_conn_string(str(path)) as b:
        a.put(config(thread), root, metadata(0), {"n": "1"})
        head_a, head_b = a.get_tuple(config(thread)), b.get_tuple(config(thread))
        assert head_a is not None and head_b is not None
        a.put(head_a.config, child_a, metadata(1), {"n": "2"})
        b.put(head_b.config, child_b, metadata(1), {"n": "2"})
        latest = b.get_tuple(config(thread))
    assert lineage(path, thread) == {
        root["id"]: None,
        child_a["id"]: root["id"],
        child_b["id"]: root["id"],
    }
    assert latest is not None
    assert latest.checkpoint["id"] == max(child_a["id"], child_b["id"])


def test_sqlite_put_writes_conflict_rules(tmp_path: Path) -> None:
    """INSERT OR IGNORE for regular and mixed batches, INSERT OR REPLACE for an all-special one."""
    path = tmp_path / "checkpoints.sqlite"
    thread = new_thread()
    with SqliteSaver.from_conn_string(str(path)) as a, SqliteSaver.from_conn_string(str(path)) as b:
        saved = a.put(config(thread), checkpoint(0, {"n": 0}, {"n": "1"}), metadata(0), {"n": "1"})
        a.put_writes(saved, [("out", "from a")], "task-regular")
        b.put_writes(saved, [("out", "from b")], "task-regular")
        a.put_writes(saved, [(RESUME, "approve")], "task-special")
        b.put_writes(saved, [(RESUME, "reject")], "task-special")
        # A batch mixing regular and special channels is insert-only throughout, as on Postgres.
        a.put_writes(saved, [("out", 1), (RESUME, "first")], "task-mixed")
        b.put_writes(saved, [("out", 2), (RESUME, "second")], "task-mixed")
    assert stored_writes(path, thread) == {
        ("task-regular", "out"): "from a",
        ("task-special", RESUME): "reject",
        ("task-mixed", "out"): 1,
        ("task-mixed", RESUME): "first",
    }
