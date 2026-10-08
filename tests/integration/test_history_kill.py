"""Kill a writing process 100 times mid-stream; at most the line it was writing may be lost.

The child prints the seq of every event `emit` returned, after the event is in the file. After the
kill, every printed seq must be in the file as a complete line, so nothing the writer acknowledged
is lost; only a final partial line may be. On Linux `Popen.kill()` sends SIGKILL, and the test
checks that this is how the child died; on Windows it calls TerminateProcess.
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
    data = {"i": i, "pad": "x" * (i % 700)}
    event = writer.emit("saver.put_writes.call", thread_id="t", data=data)
    print(event.seq, flush=True)
    i += 1
"""

TRIALS = 100
SIGKILL = 9  # signal.SIGKILL on Linux; the Windows signal module does not define it


def kill_after(path: Path, min_bytes: int) -> tuple[int, list[int]]:
    """Kill the writer once the file holds min_bytes; return its exit status and printed seqs."""
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(path)], stdout=subprocess.PIPE, text=True
    )
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
        printed, _ = child.communicate()
    return child.returncode, [int(line) for line in printed.split()]


def test_killed_writer_loses_at_most_the_line_it_was_writing(tmp_path: Path) -> None:
    for trial in range(TRIALS):
        path = tmp_path / f"victim-{trial}.jsonl"
        returncode, acknowledged = kill_after(path, min_bytes=2048 * (1 + trial % 40))
        if sys.platform.startswith("linux"):
            assert returncode == -SIGKILL, f"trial {trial}: exit status {returncode}"
        else:
            assert returncode != 0, "the writer must die from the kill, not exit on its own"
        result = read_history(path)  # raises if any complete line is corrupt
        assert result.events, f"trial {trial}: no complete events"
        seqs = [e.seq for e in result.events]
        assert seqs == list(range(len(seqs)))
        assert {e.proc for e in result.events} == {"victim"}
        # Every event emit returned is in the file; the file may hold at most one more,
        # written but killed before its seq was printed.
        assert set(acknowledged) <= set(seqs), f"trial {trial}: acknowledged events lost"
        assert len(seqs) - len(acknowledged) <= 1, (
            f"trial {trial}: {len(seqs)} vs {len(acknowledged)}"
        )
