"""Record the statements PostgresSaver sends for one put and one put_writes (T-03 evidence).

Run with the compose services up; it finds the repository root itself:

    cd research/evidence/t-03
    uv run python capture_statements.py > statements.log

Statement logging is enabled only for this script's own connection (`-c log_statement=all`), so
the server configuration is unchanged. The lines are read back from `docker compose logs` and
filtered to this connection's backend process.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.base import Checkpoint
from langgraph.checkpoint.base.id import uuid6
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[3]
DSN = os.environ.get(
    "CRASHCOURT_PG_DSN", "postgresql://crashcourt:crashcourt@127.0.0.1:55432/crashcourt"
)
PREFIXED = re.compile(r"^\d{4}-\d{2}-\d{2} [\d:.]+ UTC \[(\d+)\]")


def main() -> None:
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    thread = f"audit-{uuid4()}"
    cp = Checkpoint(
        v=4,
        id=str(uuid6(clock_seq=0)),
        ts=datetime.now(UTC).isoformat(),
        channel_values={"items": [1, 2, 3], "n": 0},
        channel_versions={"items": "1", "n": "1"},
        versions_seen={},
        updated_channels=None,
    )
    config = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    # make_conninfo merges the option into any DSN, including one that already has a query string.
    with PostgresSaver.from_conn_string(
        make_conninfo(DSN, options="-c log_statement=all")
    ) as saver:
        assert isinstance(saver.conn, Connection)
        pid = saver.conn.info.backend_pid
        saved = saver.put(config, cp, {"source": "loop", "step": 0}, {"items": "1", "n": "1"})
        saver.put_writes(saved, [("out", "value")], "task-1")
    time.sleep(1)  # let the server flush its log
    logs = subprocess.run(
        ["docker", "compose", "logs", "--no-log-prefix", "--since", since, "postgres"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    print(f"# thread_id={thread} checkpoint_id={cp['id']} backend_pid={pid}")
    ours = False
    for line in logs.splitlines():
        match = PREFIXED.match(line)
        if match:
            ours = match.group(1) == str(pid)
        if ours:
            print(line)


if __name__ == "__main__":
    main()
