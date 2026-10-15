"""Did each saver mutant act as labeled in every trial of a C5 run? Read from the trials' own
records (the worker's history and the post-trial snapshot), not from the checker.

    python research/evidence/t-11/mechanisms.py <C5 runs dir> <report path>.json

- M1: every put_writes call with two or more rows left one row fewer in the snapshot.
- M2: whether any saver read named a checkpoint id (the only reads M2 changes).
- M3: every checkpoint row has no parent.
- M4: acknowledged checkpoints (put returned ok) that the snapshot does not hold.
- M5: latest reads that returned an id older than one the same worker acknowledged before.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from crashcourt.history.reader import read_history  # noqa: E402


def trial_facts(trial: Path) -> dict[str, Any]:
    events = read_history(trial / "w1.jsonl").events
    snapshot = json.loads((trial / "snapshot.json").read_text(encoding="utf-8"))["second"]
    rows = Counter((w["checkpoint_id"], w["task_id"]) for w in snapshot["writes"])
    expected: Counter[tuple[str | None, str | None]] = Counter()
    for e in events:
        if e.kind == "saver.put_writes.call":
            n = len(e.data["channels"])  # pyright: ignore[reportArgumentType]
            expected[(e.checkpoint_id, e.task_id)] += n - 1 if n >= 2 else n
    multi = sum(
        1
        for e in events
        if e.kind == "saver.put_writes.call" and len(e.data["channels"]) >= 2  # pyright: ignore[reportArgumentType]
    )
    held = {c["checkpoint_id"] for c in snapshot["checkpoints"]}
    acked = [e.checkpoint_id for e in events if e.kind == "saver.put.return" and e.status == "ok"]
    stale = 0
    seen: list[str] = []
    for e in events:
        if e.kind == "saver.put.return" and e.status == "ok" and e.checkpoint_id:
            seen.append(e.checkpoint_id)
        if (
            e.kind == "saver.get_tuple.return"
            and e.data.get("found") is True
            and e.data.get("requested") is None
            and any(e.checkpoint_id is not None and a > e.checkpoint_id for a in seen)
        ):
            stale += 1
    return {
        "writes_as_if_second_dropped": dict(rows) == dict(expected),
        "multi_row_calls": multi,
        "reads_by_id": sum(
            1 for e in events if e.kind == "saver.get_tuple.call" and e.checkpoint_id is not None
        ),
        "rows_without_parent": sum(
            1 for c in snapshot["checkpoints"] if c["parent_checkpoint_id"] is None
        ),
        "checkpoint_rows": len(snapshot["checkpoints"]),
        "acknowledged_missing": sum(1 for a in acked if a not in held),
        "stale_latest_reads": stale,
    }


def main() -> int:
    runs, report = Path(sys.argv[1]), Path(sys.argv[2])
    found: dict[str, Any] = {}
    for batch in sorted(runs.glob("c5-M*")):
        mutant = batch.name.split("-")[1]
        trials = sorted(batch.glob("*/trial-*"))
        facts = [trial_facts(t) for t in trials]
        found[mutant] = {
            "batch": batch.name,
            "trials": len(trials),
            "writes_as_if_second_dropped": sum(f["writes_as_if_second_dropped"] for f in facts),
            "trials_with_reads_by_id": sum(f["reads_by_id"] > 0 for f in facts),
            "trials_every_row_parentless": sum(
                f["rows_without_parent"] == f["checkpoint_rows"] > 0 for f in facts
            ),
            "acknowledged_missing_per_trial": sorted({f["acknowledged_missing"] for f in facts}),
            "trials_with_stale_latest_reads": sum(f["stale_latest_reads"] > 0 for f in facts),
        }
    report.write_text(json.dumps(found, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(found, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
