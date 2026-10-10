"""Are fault-free trials alike? Compare every trial of each batch by what it left behind.

    CRASHCOURT_PG_DSN=... uv run python research/evidence/t-09/determinism.py runs/<batch> ...

For each trial: the ledger rows (from the sink's history), each op's terminal client event and
output, and for each thread the saved state of its latest checkpoint and the shape of its
lineage (how many checkpoints, and whether each names the one before it as its parent), read
directly from Postgres. Everything that differs between trials by construction is removed first:
the trial's thread prefix, ledger sequence numbers and times, checkpoint ids, pids. What remains
is one signature per trial; a fault-free batch is deterministic when all its trials share one.

Checkpoint blobs are decoded with LANGGRAPH_STRICT_MSGPACK=true, as the saver's docs recommend
for reading untrusted rows. This script reads the database to report on a batch after it ran;
nothing in a run reads it to decide an outcome.
"""

from __future__ import annotations

import os

os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from crashcourt.history.reader import read_history
from crashcourt.probes import DEFAULT_PG_DSN


def plain(value: Any) -> Any:
    """Channel values as JSON: messages become [type, id, content]."""
    if hasattr(value, "type") and hasattr(value, "content"):
        return [str(value.type), str(getattr(value, "id", "")), str(value.content)]
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    return value


async def lineage(saver: AsyncPostgresSaver, thread: str) -> dict[str, Any]:
    config: Any = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    tuples = [t async for t in saver.alist(config)]
    ids = {t.config["configurable"]["checkpoint_id"] for t in tuples}
    parents = Counter(
        (t.parent_config or {}).get("configurable", {}).get("checkpoint_id") for t in tuples
    )
    head = await saver.aget_tuple(config)
    return {
        "checkpoints": len(tuples),
        "roots": parents.get(None, 0),
        # A chain: no parent has two children. A fork shows up here as a parent named twice.
        "forked_parents": sorted(n for p, n in parents.items() if p is not None and n > 1),
        "dangling": sum(1 for p in parents if p is not None and p not in ids),
        "head": plain(head.checkpoint["channel_values"]) if head else None,
    }


async def trial_signature(saver: AsyncPostgresSaver, directory: Path) -> tuple[bool, str]:
    record = json.loads((directory / "trial.json").read_text(encoding="utf-8"))
    cut = f"{record['batch']}/{record['cell']}/{record['trial']}/"
    orchestrator = read_history(directory / "orchestrator.jsonl").events
    sink = directory / "sink.jsonl"
    rows = read_history(sink).events if sink.exists() else ()
    threads = sorted({op["thread_id"] for op in record["ops"].values()})
    signature = {
        "ledger": [
            [e.effect_key, e.logical_op, e.op_id, e.attempt, e.data.get("worker")] for e in rows
        ],
        "client": {
            str(e.op_id): [e.kind, e.data.get("output")]
            for e in orchestrator
            if e.kind in ("client.ok", "client.fail", "client.info")
        },
        "threads": {t: await lineage(saver, t) for t in threads},
    }
    text = json.dumps(signature, sort_keys=True).replace(cut, "<trial>/")
    return bool(record["valid"]), text


async def report(batches: list[Path]) -> int:
    dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
    failed = 0
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        for batch in batches:
            run = json.loads((batch / "run.json").read_text(encoding="utf-8"))
            trials = sorted((batch / run["cell"]).glob("trial-*"))
            signatures: Counter[str] = Counter()
            valid = 0
            for directory in trials:
                ok, text = await trial_signature(saver, directory)
                valid += ok
                signatures[text] += 1
            print(
                f"{batch.name}: {len(trials)} trials, {valid} valid, "
                f"{len(signatures)} distinct signature(s) {sorted(signatures.values())}"
            )
            shapes: Counter[str] = Counter()
            for text, count in signatures.items():
                for thread, shape in json.loads(text)["threads"].items():
                    keys = ("checkpoints", "roots", "forked_parents", "dangling")
                    shapes[f"{thread} " + json.dumps({k: shape[k] for k in keys})] += count
            for shape, count in sorted(shapes.items()):
                print(f"  lineage {shape}: {count} trial(s)")
            first = json.loads(signatures.most_common(1)[0][0])
            print(f"  ledger: {first['ledger']}")
            for thread, shape in first["threads"].items():
                head = json.dumps(shape["head"], sort_keys=True)
                print(f"  {thread} head (most common): {head[:400]}")
            failed += valid != len(trials) or len(signatures) != 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(report([Path(p) for p in sys.argv[1:]])))
