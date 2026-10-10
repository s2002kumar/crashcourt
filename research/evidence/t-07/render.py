"""Render a merged history as one readable line per event.

    uv run python research/evidence/t-07/render.py runs/<batch>/<cell>/trial-00001/merged.jsonl

Columns: process, seq, kind, op, failpoint, the last part of the thread id, status, then the data
fields worth reading (which worker answered and its last event, which hit a release names, how a
worker ended, the client's output or reason). Errors follow as ERR=.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SHOWN = (
    "proc",
    "pid",
    "to",
    "worker",
    "worker_seq",
    "hit_seq",
    "barrier",
    "action",
    "occurrence",
    "signal",
    "returncode",
    "found",
    "stage",
    "fault",
    "output",
    "reason",
)


def render(line: str) -> str:
    event = json.loads(line)
    data = {k: v for k, v in event["data"].items() if k in SHOWN}
    thread = (event.get("thread_id") or "").rsplit("/", 1)[-1]
    text = (
        f"{event['proc']:>12} {event['seq']:>3}  {event['kind']:<23} {event.get('op_id') or '':<7}"
        f" {event.get('fp') or '':<16} {thread:<3} {event.get('status') or '':<4} "
        + " ".join(f"{k}={v}" for k, v in data.items())
    )
    if event.get("error"):
        text += f" ERR={event['error']}"
    return text.rstrip()


def main(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        print(render(line))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
