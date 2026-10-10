"""Reading a trial directory in T-07's layout, and writing the checker's outputs beside it."""

from __future__ import annotations

import json
from pathlib import Path

from crashcourt.checker.artifacts import TrialArtifacts
from crashcourt.checker.load import load_trial, write_outputs
from crashcourt.spec.records import ResultRecord
from tests.unit import _trials as T
from tests.unit.test_checker_engine import run


def write_trial(artifacts: TrialArtifacts, root: Path) -> Path:
    """Lay out a trial as the runner does, with the post-trial files the checker reads."""
    scenario = artifacts.scenario
    batch = root / scenario.batch
    directory = batch / scenario.cell / f"trial-{scenario.trial:05d}"
    directory.mkdir(parents=True)
    assert artifacts.manifest is not None
    # Bytes, not text: the manifest's sha256 is over its bytes, which the runner writes with LF.
    (batch / "manifest.json").write_bytes(artifacts.manifest.to_json().encode("utf-8"))
    for history in artifacts.histories:
        text = "".join(e.to_line() for e in history.events).encode("utf-8") + history.partial_tail
        (directory / f"{history.proc}.jsonl").write_bytes(text)
    (directory / "merged.jsonl").write_text("not read\n", encoding="utf-8")
    (directory / "trial.json").write_text(
        json.dumps({"valid": True, "invalid_reasons": []}), encoding="utf-8"
    )
    if artifacts.ledger is not None:
        (directory / "ledger.json").write_text(artifacts.ledger.model_dump_json(), encoding="utf-8")
    if artifacts.snapshots is not None:
        (directory / "snapshot.json").write_text(
            artifacts.snapshots.model_dump_json(), encoding="utf-8"
        )
    health = [h.model_dump(mode="json") for h in artifacts.sink_health]
    (directory / "sink-health.json").write_text(json.dumps(health), encoding="utf-8")
    return directory


def test_a_trial_round_trips_through_its_directory(tmp_path: Path) -> None:
    for artifacts in (T.w1_trial(), T.w5_fork_trial(), T.kill_at_writes_trial("put_writes.after")):
        directory = write_trial(artifacts, tmp_path / artifacts.scenario.cell / str(id(artifacts)))
        loaded = load_trial(directory, artifacts.scenario)
        assert loaded.harness is not None and loaded.harness.valid
        expected, got = run(artifacts), run(loaded)
        assert got.result_json() == expected.result_json()
        assert got.report_json() == expected.report_json()


def test_outputs_are_written_beside_the_trial(tmp_path: Path) -> None:
    artifacts = T.w1_trial()
    directory = write_trial(artifacts, tmp_path)
    write_outputs(directory, run(load_trial(directory, artifacts.scenario)))
    ResultRecord.model_validate_json((directory / "result.json").read_text(encoding="utf-8"))
    assert (
        json.loads((directory / "checker.json").read_text(encoding="utf-8"))["freeze_state"]
        == "FROZEN"
    )
    empty = T.lost_before_first_checkpoint_trial()
    other = write_trial(empty, tmp_path / "empty")
    write_outputs(other, run(load_trial(other, empty.scenario)))
    assert not (other / "result.json").exists() and (other / "checker.json").exists()


def test_a_missing_file_stays_missing(tmp_path: Path) -> None:
    artifacts = T.w1_trial()
    directory = write_trial(artifacts, tmp_path)
    (directory / "snapshot.json").unlink()
    out = run(load_trial(directory, artifacts.scenario))
    assert out.report.invalid is not None and out.report.invalid.trigger == 1


def test_an_unreadable_line_is_invalid(tmp_path: Path) -> None:
    artifacts = T.w1_trial()
    directory = write_trial(artifacts, tmp_path)
    path = directory / "worker-1.jsonl"
    path.write_bytes(b'{"broken": true}\n' + path.read_bytes())
    out = run(load_trial(directory, artifacts.scenario))
    assert out.report.invalid is not None and out.report.invalid.trigger == 2


def test_the_orchestrators_invalid_verdict_is_read(tmp_path: Path) -> None:
    artifacts = T.w1_trial()
    directory = write_trial(artifacts, tmp_path)
    (directory / "trial.json").write_text(
        json.dumps({"valid": False, "invalid_reasons": ["op resume-1 timed out while running"]}),
        encoding="utf-8",
    )
    out = run(load_trial(directory, artifacts.scenario))
    assert out.report.invalid is not None and out.report.invalid.trigger == 16
