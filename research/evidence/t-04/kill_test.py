"""Run the kill test's procedure once and write its counts to kill-test.txt (T-04 evidence).

The integration test asserts; this records what one run recovered. It kills a writing process
100 times, each at a different file size, with the test's own `kill_after`.

    uv run python research/evidence/t-04/kill_test.py
"""

from __future__ import annotations

import platform
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from crashcourt.history.reader import read_history

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.integration.test_history_kill import TRIALS, kill_after

OUT = Path(__file__).resolve().parent / "kill-test.txt"


def main() -> None:
    recovered = acknowledged = partial = extra = 0
    statuses: set[int] = set()
    with tempfile.TemporaryDirectory() as tmp:
        for trial in range(TRIALS):
            path = Path(tmp) / f"victim-{trial}.jsonl"
            returncode, printed = kill_after(path, min_bytes=2048 * (1 + trial % 40))
            result = read_history(path)  # raises if any complete line is corrupt
            seqs = [e.seq for e in result.events]
            assert seqs == list(range(len(seqs))), f"trial {trial}: seq not contiguous"
            assert set(printed) <= set(seqs), f"trial {trial}: acknowledged events lost"
            assert len(seqs) - len(printed) <= 1, f"trial {trial}: more than one unacknowledged"
            if sys.platform.startswith("linux"):
                assert returncode == -9, f"trial {trial}: exit status {returncode}"
            statuses.add(returncode)
            recovered += len(seqs)
            acknowledged += len(printed)
            partial += bool(result.partial_tail)
            extra += len(seqs) - len(printed)
    kill = "SIGKILL" if sys.platform.startswith("linux") else "Popen.kill() (TerminateProcess)"
    lines = [
        f"Kill test procedure (tests/integration/test_history_kill.py, kill_after), run "
        f"{datetime.now(UTC):%Y-%m-%d}",
        f"platform: {platform.system()} {platform.release()}, python {platform.python_version()}; "
        f"the kill is {kill}",
        f"kills: {TRIALS}; exit statuses seen: {sorted(statuses)}",
        f"complete events recovered: {recovered}",
        f"events acknowledged (emit returned, seq printed): {acknowledged}, all recovered",
        f"events in the file but not yet acknowledged when killed: {extra}",
        "files with a corrupt complete line: 0",
        f"files with a partial final line: {partial}",
        "every file: seq contiguous from 0",
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(OUT.read_text(encoding="utf-8"), end="")


if __name__ == "__main__":
    main()
