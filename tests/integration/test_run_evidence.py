"""The post-trial evidence on real trials, read back through the frozen checker.

Fault-free W1 trials (approve, and reject, which posts no effect) and T-07's kill-at-a-barrier
smoke scenario: control runs, never new evidence. Each trial's files must load with no error,
the checker must find nothing INVALID in them and give the same bytes twice, and a read that
fails must leave its file absent so that the checker refuses the trial (trigger 1). Linux only,
like `crashcourt run`; CI runs it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crashcourt.checker.artifacts import SnapshotPair, TrialArtifacts
from crashcourt.checker.engine import CheckerOutput, check_trial
from crashcourt.checker.load import load_trial
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256
from crashcourt.experiment.observe import mismatch
from crashcourt.experiment.preregistration import LedgerRows
from crashcourt.experiment.verify import TrialFiles
from crashcourt.history.reader import read_history
from crashcourt.orchestrator import evidence
from crashcourt.orchestrator.declared import trial_scenario
from crashcourt.orchestrator.runner import BatchSummary, RunOptions, Settings, run_batch
from crashcourt.sink.client import SinkClient, SinkError
from crashcourt.sink.ledger import TrialId
from crashcourt.snapshot import dump
from crashcourt.spec.scenario import load_scenario, trial_seed

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="crashcourt run needs Linux; CI runs it"),
]

REPO = Path(__file__).resolve().parents[2]
REGISTRY = ClauseRegistry.load(REPO / "clauses")
REGISTRY_SHA = registry_sha256(REPO / "clauses")


def run(out: Path, path: Path, trials: int = 1) -> BatchSummary:
    return run_batch(
        load_scenario(path),
        RunOptions(trials=trials, out=out),
        Settings.from_environment(REPO),
        source=path,
    )


def scenario_file(name: str) -> Path:
    return REPO / "scenarios" / f"{name}.yaml"


def checked(summary: BatchSummary, number: int = 1) -> tuple[TrialArtifacts, CheckerOutput]:
    facts = trial_scenario(summary.directory, number, evidence="control")
    artifacts = load_trial(summary.trials[number - 1].directory, facts)
    return artifacts, check_trial(artifacts, REGISTRY, registry_sha256=REGISTRY_SHA)


def outcomes(output: CheckerOutput) -> list[str]:
    text = output.result_json()
    results: list[dict[str, Any]] = json.loads(text)["results"] if text else []
    return [str(r["outcome"]) for r in results]


#: A batch, and GET /ledger and GET /ledger/verify for its last trial, read as soon as it ended:
#: the sink keeps only the last trial it opened.
Ran = tuple[BatchSummary, list[dict[str, Any]], dict[str, Any]]


@pytest.fixture(scope="module")
def approved(tmp_path_factory: pytest.TempPathFactory) -> Ran:
    summary = run(tmp_path_factory.mktemp("approve"), scenario_file("w1-approve"), trials=2)
    record = json.loads((summary.directory / "run.json").read_text(encoding="utf-8"))
    number = summary.trials[-1].number
    seed = trial_seed(record["master_seed"], record["cell"], number)
    trial = TrialId(summary.batch, record["cell"], number, seed)
    sink = SinkClient(Settings.from_environment(REPO).sink_url)
    return summary, sink.ledger(trial), sink.verify(trial)


@pytest.fixture(scope="module")
def approve(approved: Ran) -> BatchSummary:
    return approved[0]


@pytest.fixture(scope="module")
def reject(tmp_path_factory: pytest.TempPathFactory) -> BatchSummary:
    return run(tmp_path_factory.mktemp("reject"), scenario_file("w1-reject"))


@pytest.mark.parametrize("name", ["approve", "reject"])
def test_the_files_load_and_the_checker_finds_nothing_invalid(
    name: str, request: pytest.FixtureRequest
) -> None:
    summary: BatchSummary = request.getfixturevalue(name)
    for number, trial in enumerate(summary.trials, start=1):
        assert trial.valid, trial.reasons
        for file in (evidence.LEDGER_FILE, evidence.SNAPSHOT_FILE, evidence.HEALTH_FILE):
            assert (trial.directory / file).is_file(), file
        artifacts, output = checked(summary, number)
        assert artifacts.load_errors == ()
        assert output.report.invalid is None, output.report.invalid
        assert "INVALID" not in outcomes(output)


def test_the_checker_gives_the_same_bytes_twice(approve: BatchSummary) -> None:
    _, first = checked(approve)
    _, second = checked(approve)
    assert first.result_json() == second.result_json()
    assert first.report_json() == second.report_json()


def test_the_ledger_is_the_sinks_rows_exactly_and_t13_reads_it(
    approved: Ran, reject: BatchSummary
) -> None:
    summary, rows, verify = approved
    last = summary.trials[-1]
    export = json.loads((last.directory / evidence.LEDGER_FILE).read_text(encoding="utf-8"))
    assert export["rows"] == rows
    assert [r["sink_seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert export["verify_ok"] is verify["ok"] is True

    charge = LedgerRows(source="ledger", effect="charge", rows=1, attempts=(1,))
    files = TrialFiles(trial=last.number, directory=last.directory, checker={}, result=None)
    assert mismatch(charge, files) is None
    none = LedgerRows(source="ledger", effect="charge", rows=0)
    trial_dir = reject.trials[0].directory
    assert mismatch(none, TrialFiles(trial=1, directory=trial_dir, checker={}, result=None)) is None


def test_sink_health_is_the_reading_before_then_after_for_this_trial(approve: BatchSummary) -> None:
    for trial in approve.trials:
        readings = json.loads((trial.directory / evidence.HEALTH_FILE).read_text(encoding="utf-8"))
        assert [r["when"] for r in readings] == ["before", "after"]
        expected = f"{approve.batch}/{trial.directory.parent.name}/{trial.number}"
        assert {r["open_trial"] for r in readings} == {expected}
        assert not any(r["hooks_enabled"] for r in readings)


def quiescent(directory: Path, oracle_dsn: str) -> SnapshotPair:
    pair = SnapshotPair.model_validate_json((directory / evidence.SNAPSHOT_FILE).read_bytes())
    orchestrator = read_history(directory / "orchestrator.jsonl").events
    ends = [e.seq for e in orchestrator if e.kind in ("worker.exit", "fault.heal")]
    assert ends and max(ends) <= pair.first.meta.after_orchestrator_seq
    assert pair.first.meta.after_orchestrator_seq == orchestrator[-1].seq
    assert pair.second.meta.taken_ns - pair.first.meta.taken_ns >= 2_000_000_000
    assert pair.first.meta.toxics_installed == pair.second.meta.toxics_installed == ()
    assert pair.first.meta.dsn == pair.second.meta.dsn == oracle_dsn
    assert oracle_dsn == dump.redact(Settings.from_environment(REPO).direct_dsn)
    return pair


def test_the_snapshot_is_taken_after_quiescence_on_the_direct_dsn(approve: BatchSummary) -> None:
    for number, trial in enumerate(approve.trials, start=1):
        facts = trial_scenario(approve.directory, number, evidence="control")
        pair = quiescent(trial.directory, facts.oracle_dsn)
        assert pair.pretrial_rows == dict.fromkeys(facts.threads, 0)
        assert pair.first.meta.threads == facts.threads


def test_after_a_kill_the_snapshot_waits_for_the_heal(tmp_path: Path) -> None:
    summary = run(tmp_path, REPO / "tests" / "integration" / "scenarios" / "kill-parked.yaml")
    trial = summary.trials[0]
    assert trial.valid, trial.reasons
    oracle = json.loads((summary.directory / "run.json").read_text(encoding="utf-8"))["oracle_dsn"]
    quiescent(trial.directory, oracle)
    kinds = [e.kind for e in read_history(trial.directory / "orchestrator.jsonl").events]
    assert "fault.heal" in kinds


Breaker = Callable[[pytest.MonkeyPatch], None]


def sink_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def ledger(self: SinkClient, trial: TrialId) -> list[dict[str, Any]]:
        raise SinkError("GET /ledger: the sink is unreachable")

    monkeypatch.setattr(SinkClient, "ledger", ledger)


def database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def take_pair(*args: object, **kwargs: object) -> SnapshotPair:
        raise OSError("connection to the database refused")

    monkeypatch.setattr(dump, "take_pair", take_pair)


def health_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    original = SinkClient.health
    calls: list[int] = []

    def health(self: SinkClient) -> dict[str, Any]:
        answer = original(self)
        calls.append(1)
        return {**answer, "status": "starting"} if len(calls) == 1 else answer

    monkeypatch.setattr(SinkClient, "health", health)


def counts_disagree(monkeypatch: pytest.MonkeyPatch) -> None:
    original = SinkClient.verify

    def verify(self: SinkClient, trial: TrialId) -> dict[str, Any]:
        answer = original(self, trial)
        return {**answer, "rows": int(answer["rows"]) + 1}

    monkeypatch.setattr(SinkClient, "verify", verify)


@pytest.mark.parametrize(
    ("breaker", "absent"),
    [
        (sink_unreachable, (evidence.LEDGER_FILE, evidence.HEALTH_FILE)),
        (database_unreachable, (evidence.SNAPSHOT_FILE,)),
        (health_refused, (evidence.HEALTH_FILE,)),
        (counts_disagree, (evidence.LEDGER_FILE,)),
    ],
)
def test_a_failed_read_leaves_its_file_absent_and_the_checker_refuses_the_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, breaker: Breaker, absent: tuple[str, ...]
) -> None:
    breaker(monkeypatch)
    summary = run(tmp_path, scenario_file("w1-approve"))
    directory = summary.trials[0].directory
    for file in absent:
        assert not (directory / file).exists(), file
    assert not list(directory.glob("*.partial"))
    assert not summary.trials[0].valid
    _, output = checked(summary)
    assert output.report.invalid is not None and output.report.invalid.trigger == 1
