"""The orchestrator end to end: real worker processes, the control socket, Postgres through each
worker's own Toxiproxy route, and the effect sink. Linux only, like `crashcourt run`; CI runs it.

Each test runs a batch the way the CLI does (doctor, preflight, trials) and then reads back what
the trial wrote: the orchestrator's client view, each process's history, and the merged history.
"""

from __future__ import annotations

import json
import sys
import uuid
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
SCENARIOS = Path(__file__).parent / "scenarios"
TERMINAL = ("client.ok", "client.fail", "client.info")


def run(tmp_path: Path, path: Path, trials: int = 1) -> BatchSummary:
    return run_batch(
        load_scenario(path),
        RunOptions(trials=trials, out=tmp_path),
        Settings.from_environment(REPO),
        source=path,
    )


def history(summary: BatchSummary, name: str, trial: int = 1) -> tuple[Event, ...]:
    return read_history(summary.trials[trial - 1].directory / f"{name}.jsonl").events


def trial_record(summary: BatchSummary, trial: int = 1) -> dict[str, Any]:
    return json.loads((summary.trials[trial - 1].directory / "trial.json").read_text())


def one_terminal_each(events: tuple[Event, ...]) -> dict[str, str]:
    """Every invoke has exactly one terminal event; returns op -> outcome."""
    invoked = [e.op_id for e in events if e.kind == "client.invoke"]
    assert len(invoked) == len(set(invoked))
    ended: dict[str, str] = {}
    for e in events:
        if e.kind in TERMINAL:
            assert e.op_id in invoked and e.op_id not in ended
            ended[str(e.op_id)] = e.kind
    assert sorted(ended) == sorted(str(op) for op in invoked)
    return ended


def test_two_workers_and_100_sequential_operations(tmp_path: Path) -> None:
    summary = run(tmp_path, SCENARIOS / "sequential-100.yaml")
    trial = summary.trials[0]
    assert trial.valid, trial.reasons
    ended = one_terminal_each(history(summary, "orchestrator"))
    assert len(ended) == 100 and set(ended.values()) == {"client.ok"}
    merged = read_history(trial.directory / "merged.jsonl").events
    assert {e.proc for e in merged} == {"orchestrator", "w1", "w2"}
    # The last op continues a thread both workers wrote: ten runs of two nodes each.
    last = next(
        e for e in history(summary, "orchestrator") if e.op_id == "op-100" and e.kind == "client.ok"
    )
    assert last.data["output"] == {"trace": ["first", "second"] * 10}


def test_both_workers_pause_at_get_tuple_after_and_are_released_together(tmp_path: Path) -> None:
    summary = run(tmp_path, REPO / "scenarios" / "smoke.yaml")
    trial = summary.trials[0]
    assert trial.valid, trial.reasons
    merged = read_history(trial.directory / "merged.jsonl").events
    hits = [i for i, e in enumerate(merged) if e.kind == "fp.hit"]
    releases = [i for i, e in enumerate(merged) if e.kind == "fp.release"]
    assert len(hits) == 2 and len(releases) == 2
    assert max(hits) < min(releases)
    assert {merged[i].proc for i in hits} == {"w1", "w2"}
    assert all(merged[i].fp == "get_tuple.after" for i in hits)
    # Each worker acts on its hit only after the release names it.
    for i in releases:
        release = merged[i]
        action = next(
            j
            for j, e in enumerate(merged)
            if e.kind == "fp.action"
            and e.proc == release.data["proc"]
            and e.data["hit_seq"] == release.data["hit_seq"]
        )
        assert action > i
    assert set(one_terminal_each(history(summary, "orchestrator")).values()) == {"client.ok"}


def test_a_worker_killed_while_parked_yields_info_and_an_exit_with_the_signal(
    tmp_path: Path,
) -> None:
    summary = run(tmp_path, SCENARIOS / "kill-parked.yaml")
    trial = summary.trials[0]
    assert trial.valid, trial.reasons
    orchestrator = history(summary, "orchestrator")
    assert one_terminal_each(orchestrator) == {"op-1": "client.info", "op-2": "client.ok"}
    exit_w1 = next(e for e in orchestrator if e.kind == "worker.exit" and e.data["proc"] == "w1")
    assert exit_w1.data["signal"] == "SIGKILL" and exit_w1.data["returncode"] == -9
    info = next(e for e in orchestrator if e.kind == "client.info")
    assert "SIGKILL" in str(info.data["reason"])
    inject = next(e for e in orchestrator if e.kind == "fault.inject")
    heal = next(e for e in orchestrator if e.kind == "fault.heal")
    assert inject.fault_id == heal.fault_id == "step-1" and heal.data["returncode"] == -9
    # w1 never acted on the hit it was parked at, and that put never returned. (Other saver
    # calls may follow the hit: under sync durability LangGraph runs put_writes beside put.)
    w1 = history(summary, "w1")
    [hit] = [e for e in w1 if e.kind == "fp.hit"]
    assert hit.fp == "put.before"
    assert not any(e.kind == "fp.action" for e in w1)
    assert not any(
        e.kind == "saver.put.return" and e.checkpoint_id == hit.checkpoint_id for e in w1
    )
    assert not any(e.kind == "client.fail" for e in orchestrator)


def test_a_worker_that_kills_itself_at_a_failpoint_is_explained_by_its_step(
    tmp_path: Path,
) -> None:
    summary = run(tmp_path, SCENARIOS / "self-kill.yaml")
    trial = summary.trials[0]
    assert trial.valid, trial.reasons
    orchestrator = history(summary, "orchestrator")
    assert one_terminal_each(orchestrator) == {"op-1": "client.info", "op-2": "client.ok"}
    w1 = history(summary, "w1")
    assert [e.kind for e in w1[-2:]] == ["fp.hit", "fp.action"]
    assert trial_record(summary)["workers"]["w1"]["signal"] == "SIGKILL"


def test_a_reused_batch_id_never_continues_another_batchs_threads(tmp_path: Path) -> None:
    # The output directory differs, so only the database and the sink can tell.
    path = REPO / "scenarios" / "smoke.yaml"
    batch = f"t07-reuse-{uuid.uuid4().hex[:12]}"  # unique to this run, then given twice
    first = run_batch(
        load_scenario(path),
        RunOptions(trials=1, out=tmp_path / "first", batch=batch),
        Settings.from_environment(REPO),
    )
    assert first.trials[0].valid, first.trials[0].reasons
    second = run_batch(
        load_scenario(path),
        RunOptions(trials=1, out=tmp_path / "second", batch=batch),
        Settings.from_environment(REPO),
    )
    [trial] = second.trials
    assert not trial.valid
    assert any("already holds rows for this trial's threads" in r for r in trial.reasons)
    # Nothing ran: no client event at all.
    assert not any(e.kind.startswith("client.") for e in history(second, "orchestrator"))


def test_a_run_past_its_timeout_yields_info_and_the_trial_still_finishes_cleanly(
    tmp_path: Path,
) -> None:
    summary = run(tmp_path, SCENARIOS / "timeout.yaml")
    trial = summary.trials[0]
    assert trial.valid, trial.reasons
    orchestrator = history(summary, "orchestrator")
    assert one_terminal_each(orchestrator) == {"op-1": "client.info", "op-2": "client.ok"}
    info = next(e for e in orchestrator if e.kind == "client.info")
    assert info.data["reason"] == "no result within 3 s"
    exits = {e.data["proc"]: e.data for e in orchestrator if e.kind == "worker.exit"}
    assert exits["w1"]["exit_code"] == 0 and exits["w2"]["exit_code"] == 0
    assert history(summary, "w1")[-1].kind == "fp.hit"  # still parked when it was shut down
