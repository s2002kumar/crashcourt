"""Control C9's driver on the real stack: development batches of the two single-writer, fault-free
shapes, each trial's Postgres final state against the same operations on InMemorySaver in this
process. Linux only, like `crashcourt run`; CI runs it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from crashcourt.orchestrator.runner import RunOptions, Settings, run_batch
from crashcourt.spec.scenario import load_scenario
from tests.controls.c9 import run_c9

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="crashcourt run needs Linux; CI runs it"),
]

REPO = Path(__file__).resolve().parents[2]
TRIALS = 2


def test_the_sequential_reference_equals_postgres_on_both_workloads(tmp_path: Path) -> None:
    batches: list[Path] = []
    for name in ("w1-approve", "w5-sequential"):
        path = REPO / "scenarios" / f"{name}.yaml"
        summary = run_batch(
            load_scenario(path),
            RunOptions(trials=TRIALS, out=tmp_path / "runs"),
            Settings.from_environment(REPO),
            source=path,
        )
        assert all(t.valid for t in summary.trials)
        batches.append(summary.directory)
    report = run_c9(batches, tmp_path / "scratch")
    assert report.passed
    for b in report.batches:
        assert (b.trials, b.equal, b.differing, b.undecodable) == (TRIALS, TRIALS, {}, {})
        assert b.authorized is False  # development batches, not the gate's
    assert report.checker.checker_sha256.startswith("d80951681c2b")
