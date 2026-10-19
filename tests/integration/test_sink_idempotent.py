"""The idempotent effect sink through real workers and the sink process: K1a's shape, where the
replacement requests the charge again. The sink applies the first request and records the
second as a duplicate; both are rows of the ledger, the ledger is what says so, and the worker's
response event carries the answer. A plain batch of the same shape is unchanged. Linux only, like
`crashcourt run`; CI runs it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from crashcourt.history.reader import read_history
from crashcourt.orchestrator.runner import BatchSummary, RunOptions, Settings, run_batch
from crashcourt.spec.scenario import load_scenario

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="crashcourt run needs Linux; CI runs it"),
]

REPO = Path(__file__).resolve().parents[2]
SHAPES = Path(__file__).parent / "scenarios"


def run(tmp: Path, name: str, trials: int = 2) -> BatchSummary:
    path = SHAPES / f"{name}.yaml"
    summary = run_batch(
        load_scenario(path),
        RunOptions(trials=trials, out=tmp),
        Settings.from_environment(REPO),
        source=path,
    )
    for trial in summary.trials:
        assert trial.valid, trial.reasons
    return summary


def charges(directory: Path) -> list[dict[str, object]]:
    export = json.loads((directory / "ledger.json").read_text(encoding="utf-8"))
    assert export["verify_ok"] is True
    return [r for r in export["rows"] if str(r["effect_key"]).startswith("charge:")]


def test_the_replacements_charge_is_a_duplicate_and_both_attempts_are_rows(
    tmp_path: Path,
) -> None:
    summary = run(tmp_path, "k1a-shape-idempotent")
    run_record = json.loads((summary.directory / "run.json").read_text(encoding="utf-8"))
    assert run_record["idempotent_effects"] is True and "fence" not in run_record
    for trial in summary.trials:
        record = json.loads((trial.directory / "trial.json").read_text(encoding="utf-8"))
        assert record["idempotent_effects"] is True
        rows = charges(trial.directory)
        assert [(r["sink_seq"], r["attempt"], r["worker"], r["status"]) for r in rows] == [
            (1, 1, "w1", "applied"),
            (2, 2, "w2", "duplicate"),
        ]
        assert len({r["logical_op"] for r in rows}) == 1
        # The replacement was told, in the answer it recorded; the kill took w1's answer.
        responses = [
            e
            for e in read_history(trial.directory / "w2.jsonl").events
            if e.kind == "effect.response"
        ]
        assert [e.data for e in responses] == [
            {"sink_seq": 2, "status": "duplicate", "duplicate_of": 1}
        ]
        sink = read_history(trial.directory / "sink.jsonl").events
        assert [e.data.get("status") for e in sink] == [None, "duplicate"]


def test_a_plain_batch_of_the_same_shape_applies_both(tmp_path: Path) -> None:
    summary = run(tmp_path, "t11-k1a-shape", trials=1)
    run_record = json.loads((summary.directory / "run.json").read_text(encoding="utf-8"))
    assert "idempotent_effects" not in run_record
    [trial] = summary.trials
    record = json.loads((trial.directory / "trial.json").read_text(encoding="utf-8"))
    assert "idempotent_effects" not in record
    assert [(r["attempt"], r["status"]) for r in charges(trial.directory)] == [
        (1, "applied"),
        (2, "applied"),
    ]
    responses = [
        e for e in read_history(trial.directory / "w2.jsonl").events if e.kind == "effect.response"
    ]
    assert [e.data for e in responses] == [{"sink_seq": 2}]
