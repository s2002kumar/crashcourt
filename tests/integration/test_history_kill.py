"""Kill a writing process 100 times mid-stream; everything it wrote before the kill must parse.

On Linux `Popen.kill()` sends SIGKILL; on Windows it calls TerminateProcess. Either way the
writer gets no chance to clean up, which is the point.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from crashcourt.history.reader import read_history

pytestmark = pytest.mark.integration

CHILD = """
import sys
from pathlib import Path
from crashcourt.history.writer import HistoryWriter, RunIdentity

writer = HistoryWriter(
    Path(sys.argv[1]), run=RunIdentity("DEMO-kill", "DEMO-kill", 0, "s"), proc="victim"
)
i = 0
while True:
    writer.emit("saver.put_writes.call", thread_id="t", data={"i": i, "pad": "x" * (i % 700)})
    i += 1
"""

TRIALS = 100


def kill_after(path: Path, min_bytes: int) -> int:
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(path)])
    deadline = time.monotonic() + 30
    try:
        while not (path.exists() and path.stat().st_size >= min_bytes):
            if child.poll() is not None:
                raise AssertionError(f"writer exited early with {child.returncode}")
            if time.monotonic() > deadline:
                raise AssertionError("writer produced too little output in 30 s")
            time.sleep(0.001)
    finally:
        child.kill()
        child.wait()
    return child.returncode


def test_killed_writer_loses_at_most_the_line_it_was_writing(tmp_path: Path) -> None:
    for trial in range(TRIALS):
        path = tmp_path / f"victim-{trial}.jsonl"
        returncode = kill_after(path, min_bytes=2048 * (1 + trial % 40))
        assert returncode != 0, "the writer must die from the kill, not exit on its own"
        result = read_history(path)  # raises if any complete line is corrupt
        assert result.events, f"trial {trial}: no complete events"
        assert [e.seq for e in result.events] == list(range(len(result.events)))
        assert {e.proc for e in result.events} == {"victim"}
