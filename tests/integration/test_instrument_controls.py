"""The instrument cells' shapes on the real stack, judged by the frozen checker: C3 (failpoints
armed with action none and a zero-latency toxic) on W1 and W5, K1a (langgraph#8039: a kill at
L-EXW, then the replacement takes the resume over), and K3 (langgraph#8796, held: a stale worker
held at L-T1 while another takes the turn over, then released).

Controls and published replications only: never a new-evidence cell. Each trial is compared with
the pre-registration's prediction for the cell of the same shape, read from the committed draft,
outcomes and observations alike. Linux only, like `crashcourt run`; CI runs it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crashcourt.checker.engine import check_trial
from crashcourt.checker.load import load_trial, write_outputs
from crashcourt.checker.status import EvidenceClass
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256
from crashcourt.experiment.classify import NotClassified, classify_batch
from crashcourt.experiment.observe import mismatch
from crashcourt.experiment.preregistration import EVERY_INVARIANT, PREREGISTRATION_PATH, parse
from crashcourt.experiment.verify import TrialFiles, read_trials
from crashcourt.history.reader import read_history
from crashcourt.orchestrator.declared import trial_scenario
from crashcourt.orchestrator.runner import BatchSummary, RunOptions, Settings, run_batch
from crashcourt.spec.scenario import load_scenario

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="crashcourt run needs Linux; CI runs it"),
]

REPO = Path(__file__).resolve().parents[2]
SHAPES = REPO / "tests" / "integration" / "scenarios"
REGISTRY = ClauseRegistry.load(REPO / "clauses")
REGISTRY_SHA = registry_sha256(REPO / "clauses")
PREREG = parse((REPO / PREREGISTRATION_PATH).read_text(encoding="utf-8"))
TRIALS = 2


def judged(
    tmp: Path,
    name: str,
    evidence: EvidenceClass = "control",
    replication_of: str | None = None,
) -> tuple[BatchSummary, list[TrialFiles]]:
    """A batch of the shape, every trial valid, judged by the frozen checker (outputs written)."""
    path = SHAPES / f"t11-{name}.yaml"
    summary = run_batch(
        load_scenario(path),
        RunOptions(trials=TRIALS, out=tmp),
        Settings.from_environment(REPO),
        source=path,
    )
    files: list[TrialFiles] = []
    for trial in summary.trials:
        assert trial.valid, trial.reasons
        facts = trial_scenario(
            summary.directory, trial.number, evidence=evidence, replication_of=replication_of
        )
        output = check_trial(
            load_trial(trial.directory, facts), REGISTRY, registry_sha256=REGISTRY_SHA
        )
        assert output.report.invalid is None, output.report.invalid
        write_outputs(trial.directory, output)
        result = output.result_json()
        files.append(
            TrialFiles(
                trial.number,
                trial.directory,
                json.loads(output.report_json()),
                json.loads(result) if result is not None else None,
            )
        )
    return summary, files


def as_predicted(cell_id: str, trial: TrialFiles) -> list[str]:
    """Where the trial differs from the pre-registered cell's outcomes and observations."""
    cell = PREREG.cell(cell_id)
    results = {r["invariant"]: r["outcome"] for r in (trial.result or {}).get("results", [])}
    not_exercised = {n["invariant"] for n in trial.checker.get("not_exercised", [])}
    named = {p.invariant for p in cell.predicted}
    found: list[str] = []
    for p in cell.predicted:
        names = (
            [i for i in {*results, *not_exercised} if i not in named]
            if p.invariant == EVERY_INVARIANT
            else [p.invariant]
        )
        for invariant in names:
            value = results.get(invariant, "NOT_EXERCISED" if invariant in not_exercised else None)
            if value not in p.expect:
                found.append(f"{invariant} is {value}, predicted {' or '.join(p.expect)}")
    for o in cell.observations:
        if (problem := mismatch(o, trial)) is not None:
            found.append(f"{o.label()}: {problem}")
    return found


Check = Callable[[TrialFiles], None]


@pytest.mark.parametrize(("name", "cell"), [("c3-w1", "C3-w1-sync"), ("c3-w5", "C3-w5-sync")])
def test_the_placebo_changes_nothing_and_every_inert_arm_is_hit(
    tmp_path: Path, name: str, cell: str
) -> None:
    summary, trials = judged(tmp_path, name)
    scenario = load_scenario(SHAPES / f"t11-{name}.yaml")
    for trial in trials:
        assert as_predicted(cell, trial) == []
        record: dict[str, Any] = json.loads((trial.directory / "trial.json").read_text())
        [toxic] = record["toxics"]
        assert (toxic["installed"], toxic["removed"]) == (True, True)
        assert (toxic["type"], toxic["attributes"]) == ("latency", {"jitter": 0, "latency": 0})
        hits = [e for e in read_history(trial.directory / "w1.jsonl").events if e.kind == "fp.hit"]
        assert len(hits) == len(scenario.schedule)
        assert {str(e.data["action"]) for e in hits} == {"none"}
        snapshot = json.loads((trial.directory / "snapshot.json").read_text())
        assert snapshot["first"]["meta"]["toxics_installed"] == []
    run = json.loads((summary.directory / "run.json").read_text())
    assert "saver_mutant" not in run


def test_k1a_shape_replicates_8039_as_preregistered(tmp_path: Path) -> None:
    _, trials = judged(tmp_path, "k1a-shape", "replication", "langgraph#8039")
    for trial in trials:
        assert as_predicted("K1a", trial) == []
        events = read_history(trial.directory / "orchestrator.jsonl").events
        [redeliver] = [e for e in events if e.kind == "dispatch.redeliver"]
        assert (redeliver.op_id, redeliver.data["to"]) == ("resume-1", "w2")
        exits = [e for e in events if e.kind == "worker.exit" and e.data["proc"] == "w1"]
        assert exits and exits[0].seq < redeliver.seq and exits[0].data["signal"] == "SIGKILL"
        terminals = [e for e in events if e.kind.startswith("client.") and e.op_id == "resume-1"]
        assert [(e.kind, e.data.get("worker")) for e in terminals] == [
            ("client.invoke", "w1"),
            ("client.ok", "w2"),
        ]


def test_a_batch_is_classified_once_and_identically_again(tmp_path: Path) -> None:
    """`crashcourt experiment classify` on K1a's shape: every trial as the draft predicts K1a, the
    checker run again on the same histories byte-identical, and a verdict never replaced."""
    path = SHAPES / "t11-k1a-shape.yaml"
    summary = run_batch(
        load_scenario(path),
        RunOptions(trials=TRIALS, out=tmp_path),
        Settings.from_environment(REPO),
        source=path,
    )
    first = classify_batch(summary.directory, REPO)
    assert [c.invalid for c in first] == [None] * TRIALS
    assert [as_predicted("K1a", t) for t in read_trials(summary.directory)] == [[]] * TRIALS
    again = classify_batch(summary.directory, REPO, compare=True)
    assert [c.differs for c in again] == [()] * TRIALS
    with pytest.raises(NotClassified, match="already classified"):
        classify_batch(summary.directory, REPO)


def test_k3_shape_replicates_8796_as_preregistered(tmp_path: Path) -> None:
    _, trials = judged(tmp_path, "k3-shape", "replication", "langgraph#8796")
    for trial in trials:
        assert as_predicted("K3", trial) == []
        record: dict[str, Any] = json.loads((trial.directory / "trial.json").read_text())
        assert record["ops"]["turn-1"]["redelivered_to"] == "w2"
        assert record["ops"]["turn-1"]["stale"] == ["w1 answered ok"]
        events = read_history(trial.directory / "orchestrator.jsonl").events
        order = [
            e.kind for e in events if e.kind in {"dispatch.redeliver", "client.ok", "fp.release"}
        ]
        assert order == ["dispatch.redeliver", "client.ok", "fp.release"]
