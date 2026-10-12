"""Each labeled saver mutant on the real stack, one trial of C5's shape (W1 approve, sync, one
worker, no fault), judged by the frozen checker: that the defect acts as labeled, and what checker
v1 makes of it. The full control, 20 trials per mutant against the 19-of-20 threshold, is
`python -m tests.mutants.c5`; this pins the mechanisms in CI. Linux only, like `crashcourt run`.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from crashcourt.checker.engine import CheckerOutput, check_trial
from crashcourt.checker.load import load_trial
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256
from crashcourt.history.reader import read_history
from crashcourt.orchestrator.declared import trial_scenario
from crashcourt.orchestrator.runner import BatchSummary, RunOptions, RunRefused, Settings, run_batch
from crashcourt.spec.scenario import load_scenario

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="crashcourt run needs Linux; CI runs it"),
]

REPO = Path(__file__).resolve().parents[2]
SCENARIO = REPO / "scenarios" / "w1-approve.yaml"
REGISTRY = ClauseRegistry.load(REPO / "clauses")
REGISTRY_SHA = registry_sha256(REPO / "clauses")
ANOMALY = {"EXPECTED", "GAP", "VIOLATION_CANDIDATE"}


def mutant_trial(tmp: Path, mutant: str) -> tuple[BatchSummary, CheckerOutput]:
    summary = run_batch(
        load_scenario(SCENARIO),
        RunOptions(trials=1, out=tmp, saver_mutant=mutant),
        Settings.from_environment(REPO),
        source=SCENARIO,
    )
    [trial] = summary.trials
    assert trial.valid, trial.reasons
    facts = trial_scenario(summary.directory, 1, evidence="control")
    output = check_trial(load_trial(trial.directory, facts), REGISTRY, registry_sha256=REGISTRY_SHA)
    return summary, output


def outcomes(output: CheckerOutput) -> dict[str, str]:
    text = output.result_json()
    results: list[dict[str, Any]] = json.loads(text)["results"] if text else []
    return {str(r["invariant"]): str(r["outcome"]) for r in results}


def anomalies(output: CheckerOutput) -> set[str]:
    return {k for k, v in outcomes(output).items() if v in ANOMALY}


def test_the_mutant_is_named_in_every_record(tmp_path: Path) -> None:
    summary, _ = mutant_trial(tmp_path, "M3")
    run = json.loads((summary.directory / "run.json").read_text())
    trial = summary.trials[0].directory
    record = json.loads((trial / "trial.json").read_text())
    start = next(
        e for e in read_history(trial / "orchestrator.jsonl").events if e.kind == "worker.start"
    )
    assert run["saver_mutant"] == record["saver_mutant"] == start.data["saver_mutant"] == "M3"


def test_m1_drops_the_second_row_and_v1_never_reads_it_back(tmp_path: Path) -> None:
    summary, output = mutant_trial(tmp_path, "M1")
    trial = summary.trials[0].directory
    calls = [
        e for e in read_history(trial / "w1.jsonl").events if e.kind == "saver.put_writes.call"
    ]
    rows = Counter(
        (w["checkpoint_id"], w["task_id"])
        for w in json.loads((trial / "snapshot.json").read_text())["first"]["writes"]
    )
    # One task can write to one checkpoint in several calls (an interrupt, then the resume).
    expected: Counter[tuple[str | None, str | None]] = Counter()
    for call in calls:
        written = len(call.data["channels"])  # pyright: ignore[reportArgumentType]
        expected[(call.checkpoint_id, call.task_id)] += written - 1 if written >= 2 else written
    assert any(len(c.data["channels"]) >= 2 for c in calls)  # pyright: ignore[reportArgumentType]
    assert dict(rows) == dict(expected)
    assert output.report.invalid is None
    assert anomalies(output) == set()  # W1 runs on without ever reading the rows back


def test_m2_never_acts_because_nothing_reads_by_id(tmp_path: Path) -> None:
    summary, output = mutant_trial(tmp_path, "M2")
    reads = [
        e
        for e in read_history(summary.trials[0].directory / "w1.jsonl").events
        if e.kind == "saver.get_tuple.call"
    ]
    assert reads and all(e.checkpoint_id is None for e in reads)
    assert output.report.invalid is None and anomalies(output) == set()


def test_m3_is_caught_by_i5a(tmp_path: Path) -> None:
    _, output = mutant_trial(tmp_path, "M3")
    assert output.report.invalid is None
    assert outcomes(output)["I5a"] == "GAP"


def test_m4_is_caught_by_i1b(tmp_path: Path) -> None:
    _, output = mutant_trial(tmp_path, "M4")
    assert output.report.invalid is None
    assert outcomes(output)["I1b"] == "VIOLATION_CANDIDATE"


def test_m5_on_w1_is_invalid_by_the_i3_alarm_before_i6b_is_judged(tmp_path: Path) -> None:
    # The stale read makes the resume start from the checkpoint before the interrupt: approval
    # interrupts again and the run returns ok without its charge, which v1's I3 alarm takes for
    # a harness fault (trigger 15).
    _, output = mutant_trial(tmp_path, "M5")
    assert output.report.invalid is not None and output.report.invalid.trigger == 15
    assert "returned ok but the ledger holds no charge" in output.report.invalid.reason


def test_a_mutant_never_runs_canonical(tmp_path: Path) -> None:
    with pytest.raises(RunRefused, match="canonical"):
        run_batch(
            load_scenario(SCENARIO),
            RunOptions(trials=1, out=tmp_path, canonical=True, saver_mutant="M3"),
            Settings.from_environment(REPO),
            source=SCENARIO,
        )
