"""Direct-saver audit of langgraph-checkpoint-sqlite 3.1.1 (T-03): characterization, one file.

Two SqliteSaver instances on separate connections to one database file stand in for two workers.
These record how the contrast implementation behaves; SQLite makes no multi-writer claim either.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


def test_two_sqlite_savers_fork_one_parent(tmp_path: Path) -> None:
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
    """INSERT OR IGNORE for regular writes, INSERT OR REPLACE for an all-special batch."""
    path = tmp_path / "checkpoints.sqlite"
    thread = new_thread()
    with SqliteSaver.from_conn_string(str(path)) as a, SqliteSaver.from_conn_string(str(path)) as b:
        saved = a.put(config(thread), checkpoint(0, {"n": 0}, {"n": "1"}), metadata(0), {"n": "1"})
        a.put_writes(saved, [("out", "from a")], "task-regular")
        b.put_writes(saved, [("out", "from b")], "task-regular")
        a.put_writes(saved, [(RESUME, "approve")], "task-special")
        b.put_writes(saved, [(RESUME, "reject")], "task-special")
        stored = b.get_tuple(saved)
    assert stored is not None
    values = {(task, channel): value for task, channel, value in stored.pending_writes or []}
    assert values == {("task-regular", "out"): "from a", ("task-special", RESUME): "reject"}
