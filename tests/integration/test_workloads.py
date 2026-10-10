"""W1 and W5 end to end: real workers, Postgres through each worker's proxy, the effect sink.

Each test runs a scenario from scenarios/ as `crashcourt run` does, a few trials at a time, and
reads back what the trials wrote: the client's view, each process's history, and the sink's
ledger rows. Linux only, like `crashcourt run`; CI runs it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from crashcourt.history.reader import read_history
from crashcourt.orchestrator.runner import BatchSummary, RunOptions, Settings, run_batch
from crashcourt.spec.events import Event
from crashcourt.spec.scenario import load_scenario

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="crashcourt run needs Linux; CI runs it"),
]

REPO = Path(__file__).resolve().parents[2]
TRIALS = 3


def run(tmp_path: Path, name: str, trials: int = TRIALS) -> BatchSummary:
    path = REPO / "scenarios" / f"{name}.yaml"
    summary = run_batch(
        load_scenario(path),
        RunOptions(trials=trials, out=tmp_path),
        Settings.from_environment(REPO),
        source=path,
    )
    for trial in summary.trials:
        assert trial.valid, trial.reasons
    return summary


def events(summary: BatchSummary, trial: int, proc: str) -> tuple[Event, ...]:
    path = summary.trials[trial].directory / f"{proc}.jsonl"
    return read_history(path).events if path.exists() else ()


def prefix(summary: BatchSummary, trial: int) -> str:
    record = json.loads((summary.trials[trial].directory / "trial.json").read_text())
    return f"{record['batch']}/{record['cell']}/{record['trial']}/"


def outputs(summary: BatchSummary, trial: int) -> dict[str, Any]:
    """Each op's terminal event: kind and output, as the client saw them."""
    return {
        str(e.op_id): (e.kind, e.data.get("output"))
        for e in events(summary, trial, "orchestrator")
        if e.kind in ("client.ok", "client.fail", "client.info")
    }


def ledger(summary: BatchSummary, trial: int) -> list[tuple[str, int, str, str]]:
    rows = [e for e in events(summary, trial, "sink") if e.kind == "sink.append"]
    cut = prefix(summary, trial)
    return [
        (
            str(e.effect_key).replace(cut, ""),
            int(e.attempt or 0),
            str(e.data["worker"]),
            str(e.op_id),
        )
        for e in rows
    ]


def normalized(summary: BatchSummary, trial: int) -> str:
    """Outputs and ledger with the trial's thread prefix removed: equal across fault-free trials."""
    text = json.dumps([outputs(summary, trial), ledger(summary, trial)], sort_keys=True)
    return text.replace(prefix(summary, trial), "")


def deterministic(summary: BatchSummary) -> None:
    assert len({normalized(summary, n) for n in range(len(summary.trials))}) == 1


def test_w1_approve_fires_one_charge_and_saves_approve(tmp_path: Path) -> None:
    summary = run(tmp_path, "w1-approve")
    for n in range(TRIALS):
        assert ledger(summary, n) == [("charge:t1", 1, "w1", "resume-1")]
        out = outputs(summary, n)
        assert out["start-1"][0] == "client.ok" and out["start-1"][1]["interrupted"] is True
        kind, final = out["resume-1"]
        assert kind == "client.ok" and final["decision"] == "approve"
        assert final["executed"] == [[f"charge:{prefix(summary, n)}t1", 1]]
        # The request records the decision the executing attempt acted on: the resume value.
        [request] = [e for e in events(summary, n, "w1") if e.kind == "effect.request"]
        invoke = next(
            e
            for e in events(summary, n, "orchestrator")
            if e.kind == "client.invoke" and e.op_id == "resume-1"
        )
        assert request.data["decision"] == invoke.data["resume"] == "approve"
    deterministic(summary)


def test_w1_reject_fires_nothing_and_saves_reject(tmp_path: Path) -> None:
    summary = run(tmp_path, "w1-reject")
    for n in range(TRIALS):
        assert ledger(summary, n) == []
        assert not any(e.kind.startswith("effect.") for e in events(summary, n, "w1"))
        kind, final = outputs(summary, n)["resume-1"]
        assert kind == "client.ok" and final["decision"] == "reject" and final["executed"] == []
    deterministic(summary)


def test_w1_misuse_fires_precheck_twice_every_time(tmp_path: Path) -> None:
    summary = run(tmp_path, "w1-misuse")
    for n in range(TRIALS):
        assert ledger(summary, n) == [
            ("precheck:t1", 1, "w1", "start-1"),
            ("precheck:t1", 2, "w1", "resume-1"),
            ("charge:t1", 1, "w1", "resume-1"),
        ]
    deterministic(summary)


def test_node_events_carry_the_task_id_their_writes_carry(tmp_path: Path) -> None:
    summary = run(tmp_path, "w1-misuse", trials=1)
    worker = events(summary, 0, "w1")
    nodes = [e for e in worker if e.kind in ("node.start", "node.end")]
    assert nodes and all(e.task_id and e.attempt for e in nodes)
    tasks: dict[str, set[str | None]] = {}
    for e in nodes:
        tasks.setdefault(str(e.data["node"]), set()).add(e.task_id)
    for e in worker:
        if e.kind != "saver.put_writes.call":
            continue
        node = str(e.data["task_path"]).rsplit(", ", 1)[-1]
        if node in tasks:
            assert {e.task_id} == tasks[node], (node, e.task_id)
    assert set(tasks) == {"plan", "approval", "execute", "record"}


def test_w5_one_worker_takes_both_turns_in_one_chain(tmp_path: Path) -> None:
    summary = run(tmp_path, "w5-sequential")
    for n in range(TRIALS):
        kind, final = outputs(summary, n)["turn-2"]
        assert kind == "client.ok"
        assert [m[0] for m in final["messages"]] == ["human", "ai", "human", "ai"]
        # The saver's puts form one chain: each names the previous one as its parent.
        puts = [e for e in events(summary, n, "w1") if e.kind == "saver.put.call"]
        assert [p.parent_checkpoint_id for p in puts[1:]] == [p.checkpoint_id for p in puts[:-1]]
        assert ledger(summary, n) == []
    deterministic(summary)


def test_w5_two_workers_held_together_both_finish(tmp_path: Path) -> None:
    summary = run(tmp_path, "w5-concurrent")
    for n in range(TRIALS):
        out = outputs(summary, n)
        assert {op: kind for op, (kind, _) in out.items()} == {
            "turn-1": "client.ok",
            "turn-2": "client.ok",
            "turn-3": "client.ok",
        }
        merged = read_history(summary.trials[n].directory / "merged.jsonl").events
        hits = [i for i, e in enumerate(merged) if e.kind == "fp.hit"]
        releases = [i for i, e in enumerate(merged) if e.kind == "fp.release"]
        assert len(hits) == 2 and max(hits) < min(releases)
        # Both new turns continue the checkpoint turn-1 left: they read the same head.
        first_read = {}
        for e in merged:
            if e.kind == "saver.get_tuple.return" and e.op_id in ("turn-2", "turn-3"):
                first_read.setdefault(e.op_id, e.checkpoint_id)
        assert len(first_read) == 2 and len(set(first_read.values())) == 1
        # Each answer names its own attempt of turn 1, so the two runs are distinguishable.
        cut = prefix(summary, n)
        answers = sorted(out[op][1]["messages"][-1][1] for op in ("turn-2", "turn-3"))
        assert answers == [f"ai:{cut}t1:respond:1:1", f"ai:{cut}t1:respond:1:2"]
