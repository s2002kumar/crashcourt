"""The post-trial files' contract (crashcourt.checker.load): what the runner writes, and that
missing or malformed evidence fails closed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from crashcourt.checker.artifacts import LedgerExport, SinkHealth
from crashcourt.checker.load import load_trial
from tests.unit import _trials as T
from tests.unit.test_checker_engine import run
from tests.unit.test_checker_load import write_trial

HEALTH: dict[str, Any] = {
    "status": "ok",
    "version": "0.1.0",
    "source_sha256": T.SINK_SOURCE,
    "pid": 41,
    "mono_ns": 123,
    "ledger_version": 2,
    "trial": {"batch": "b", "cell": "c", "trial": 7, "seed": "s"},
    "resume_error": None,
    "test_hooks": False,
}


def test_sink_health_is_read_from_the_sinks_own_answer() -> None:
    """The sink's GET /health fields map onto the record: test_hooks to hooks_enabled, the open
    trial to batch/cell/trial; the rest of the answer is checked, not kept."""
    reading = SinkHealth.from_reading(HEALTH, "before")
    assert reading == SinkHealth(
        when="before", hooks_enabled=False, source_sha256=T.SINK_SOURCE, open_trial="b/c/7"
    )
    closed = SinkHealth.from_reading({**HEALTH, "trial": None}, "after")
    assert closed.open_trial is None and closed.when == "after"


@pytest.mark.parametrize(
    "change",
    [
        {"status": "starting"},
        {"resume_error": "ledger chain broken"},
        {"test_hooks": "no"},
        {"source_sha256": "not a hash"},
        {"trial": {"batch": "b", "cell": "c"}},
        {"trial": {"batch": "b", "cell": "c", "trial": True}},
        {"trial": "b/c/7"},
    ],
)
def test_an_unhealthy_or_malformed_reading_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SinkHealth.from_reading({**HEALTH, **change}, "after")


def test_the_ledger_export_is_the_sinks_rows_and_its_verdict() -> None:
    artifacts = T.w1_trial()
    assert artifacts.ledger is not None and artifacts.ledger.rows
    rows = [r.model_dump(mode="json") for r in artifacts.ledger.rows]
    export = LedgerExport.from_sink(rows, {"ok": True, "rows": len(rows), "first_bad": None})
    assert export == artifacts.ledger
    broken = LedgerExport.from_sink(rows, {"ok": False, "rows": len(rows), "first_bad": 1})
    assert not broken.verify_ok
    assert LedgerExport.from_sink([], {"ok": True, "rows": 0}).rows == ()
    with pytest.raises(ValueError, match="counted"):
        LedgerExport.from_sink(rows, {"ok": True, "rows": len(rows) + 1})
    with pytest.raises(ValueError, match="malformed"):
        LedgerExport.from_sink(rows, {"ok": "yes", "rows": len(rows)})
    with pytest.raises(ValueError):
        LedgerExport.from_sink([{**rows[0], "extra": 1}], {"ok": True, "rows": 1})


def test_the_files_round_trip_from_the_models_json(tmp_path: Path) -> None:
    """The runner writes each file as the model's JSON; the checker reads back the same trial."""
    for artifacts in (T.w1_trial(), T.kill_at_writes_trial("put_writes.after")):
        directory = write_trial(artifacts, tmp_path / str(id(artifacts)))
        loaded = load_trial(directory, artifacts.scenario)
        assert loaded.load_errors == ()
        assert loaded.ledger == artifacts.ledger
        assert loaded.snapshots == artifacts.snapshots
        assert loaded.sink_health == artifacts.sink_health
        assert run(loaded).report.invalid is None


@pytest.mark.parametrize("name", ["ledger.json", "snapshot.json", "sink-health.json"])
def test_an_absent_file_is_invalid(tmp_path: Path, name: str) -> None:
    artifacts = T.w1_trial("reject")
    directory = write_trial(artifacts, tmp_path)
    (directory / name).unlink()
    out = run(load_trial(directory, artifacts.scenario))
    assert out.report.invalid is not None and out.report.invalid.trigger == 1


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("ledger.json", "{"),
        ("ledger.json", json.dumps({"rows": []})),
        ("snapshot.json", json.dumps({"first": {}, "second": {}, "pretrial_rows": {}})),
        ("sink-health.json", json.dumps({"when": "after"})),
        ("sink-health.json", json.dumps([{"when": "during"}])),
    ],
)
def test_a_malformed_file_is_invalid(tmp_path: Path, name: str, content: str) -> None:
    artifacts = T.w1_trial()
    directory = write_trial(artifacts, tmp_path)
    (directory / name).write_text(content, encoding="utf-8")
    loaded = load_trial(directory, artifacts.scenario)
    assert any(e.startswith(name) for e in loaded.load_errors)
    out = run(loaded)
    assert out.report.invalid is not None and out.report.invalid.trigger == 1
    assert name in out.report.invalid.reason


def test_both_sink_readings_are_required(tmp_path: Path) -> None:
    artifacts = T.w1_trial("reject")
    directory = write_trial(artifacts, tmp_path)
    only_after = [h.model_dump(mode="json") for h in artifacts.sink_health if h.when == "after"]
    (directory / "sink-health.json").write_text(json.dumps(only_after), encoding="utf-8")
    out = run(load_trial(directory, artifacts.scenario))
    assert out.report.invalid is not None and out.report.invalid.trigger == 1
