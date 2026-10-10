"""Crash actions in real processes: `exit` and `signal` end the worker at the named point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from crashcourt.history.reader import read_history

pytestmark = pytest.mark.integration

CHILD = """
import sys
from pathlib import Path
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from crashcourt.failpoints.registry import Arm, FailpointRegistry
from crashcourt.failpoints.saver import wrap
from crashcourt.history.writer import HistoryWriter, RunIdentity

action, point = sys.argv[2], sys.argv[3]
run = RunIdentity("DEMO-a", "DEMO-c", 0, "s")
history = HistoryWriter(Path(sys.argv[1]), run=run, proc="worker-1")
registry = FailpointRegistry("worker-1")
extra = {"signal": "SIGKILL"} if action == "signal" else {"exit_code": 7}
registry.arm(Arm(point, action, **extra))
saver = wrap(InMemorySaver(), history=history, registry=registry)
config = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
saver.put(config, empty_checkpoint(), {"source": "loop", "step": 0}, {})
print("unreachable")
"""


def run_child(tmp_path: Path, action: str, point: str) -> tuple[int, list[str], str]:
    log = tmp_path / "worker-1.jsonl"
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(log), action, point],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done.returncode, [e.kind for e in read_history(log).events], done.stdout


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ("put.before", ["saver.put.call", "fp.hit", "fp.action"]),
        ("put.after", ["saver.put.call", "fp.hit", "fp.action"]),
    ],
)
def test_exit_ends_the_process_at_the_point(
    tmp_path: Path, point: str, expected: list[str]
) -> None:
    returncode, kinds, stdout = run_child(tmp_path, "exit", point)
    assert returncode == 7
    assert kinds == expected  # never saver.put.return: the call did not complete
    assert "unreachable" not in stdout


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL to self needs POSIX; CI runs it")
def test_signal_kills_the_process_at_the_point(tmp_path: Path) -> None:
    returncode, kinds, _ = run_child(tmp_path, "signal", "put.before")
    assert returncode == -9
    assert kinds == ["saver.put.call", "fp.hit", "fp.action"]


WORKER_THREAD = """
import sys, threading
from pathlib import Path
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver
from crashcourt.failpoints.registry import Arm, FailpointRegistry
from crashcourt.failpoints.saver import wrap
from crashcourt.history.writer import HistoryWriter, RunIdentity

log, db, name = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
run = RunIdentity("DEMO-a", "DEMO-c", 0, "s")
history = HistoryWriter(log, run=run, proc="worker-1")
registry = FailpointRegistry("worker-1")
registry.arm(Arm("put_writes.before", "signal", signal=name))
with SqliteSaver.from_conn_string(db) as inner:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
    stored = inner.put(config, empty_checkpoint(), {"source": "loop", "step": 0}, {})
    saver = wrap(inner, history=history, registry=registry)
    # As LangGraph does, from a background thread, while the main thread waits.
    thread = threading.Thread(target=lambda: saver.put_writes(stored, [("n", 1)], "task-1"))
    thread.start()
    thread.join()
print("unreachable")
"""


@pytest.mark.skipif(sys.platform == "win32", reason="signals to a thread need POSIX; CI runs it")
@pytest.mark.parametrize(("name", "status"), [("SIGQUIT", -3), ("SIGABRT", -6), ("SIGSEGV", -11)])
def test_a_signal_from_a_worker_thread_ends_the_process_before_the_write(
    tmp_path: Path, name: str, status: int
) -> None:
    import sqlite3

    log, db = tmp_path / "worker-1.jsonl", tmp_path / "store.sqlite"
    done = subprocess.run(
        [sys.executable, "-c", WORKER_THREAD, str(log), str(db), name],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == status and "unreachable" not in done.stdout
    kinds = [e.kind for e in read_history(log).events]
    assert kinds == ["saver.put_writes.call", "fp.hit", "fp.action"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM writes").fetchone() == (0,)
