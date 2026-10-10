"""Read a trial directory into the checker's inputs, and write its outputs beside them.

Layout (T-07's runner, plus the two files the post-trial step adds):

| File | Read as |
| --- | --- |
| `<batch>/manifest.json` | `manifest`, and the sha256 of its bytes |
| `<trial>/<proc>.jsonl` | `histories`: complete lines, the partial tail, or the parse error |
| `<trial>/trial.json` | `harness`: the orchestrator's valid, invalid_reasons, observations |
| `<trial>/ledger.json` | `ledger`: the effect ledger's rows and the sink's verify answer |
| `<trial>/snapshot.json` | `snapshots`: the two direct snapshots and the pre-trial counts |
| `<trial>/sink-health.json` | `sink_health`: /health readings around the trial |

`merged.jsonl` is a display copy of the histories and is not read.

A file that is absent is left absent: the checker then makes the trial INVALID (trigger 1); the
loader never fills a gap. The scenario facts are the caller's: they come from the batch's scenario
and the workload's effect model, which T-07 and T-09 define.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from crashcourt.checker.artifacts import (
    HarnessRecord,
    LedgerExport,
    ProcessHistory,
    SinkHealth,
    SnapshotPair,
    TrialArtifacts,
    TrialScenario,
)
from crashcourt.checker.engine import CheckerOutput
from crashcourt.history.reader import HistoryError, read_history
from crashcourt.manifest import Manifest

DISPLAY_ONLY = frozenset({"merged.jsonl"})


def _history(path: Path) -> ProcessHistory:
    proc = path.stem
    try:
        read = read_history(path)
    except HistoryError as exc:
        return ProcessHistory(proc=proc, events=(), parse_error=str(exc))
    return ProcessHistory(proc=proc, events=read.events, partial_tail=read.partial_tail)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_trial(directory: Path, scenario: TrialScenario) -> TrialArtifacts:
    """The trial's records as the checker reads them. The batch directory is the trial's
    grandparent (`<batch>/<cell>/trial-NNNNN/`)."""
    batch = directory.parent.parent
    manifest_path = batch / "manifest.json"
    manifest = manifest_sha = None
    if manifest_path.is_file():
        raw = manifest_path.read_bytes()
        manifest = Manifest.from_json(raw.decode("utf-8"))
        manifest_sha = hashlib.sha256(raw).hexdigest()
    histories = tuple(
        _history(p)
        for p in sorted(directory.glob("*.jsonl"), key=lambda p: p.name)
        if p.name not in DISPLAY_ONLY
    )
    trial: dict[str, Any] | None = _json(directory / "trial.json")
    ledger = _json(directory / "ledger.json")
    snapshots = _json(directory / "snapshot.json")
    health = _json(directory / "sink-health.json")
    return TrialArtifacts(
        scenario=scenario,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        histories=histories,
        ledger=LedgerExport.model_validate(ledger) if ledger is not None else None,
        snapshots=SnapshotPair.model_validate(snapshots) if snapshots is not None else None,
        sink_health=tuple(SinkHealth.model_validate(h) for h in health or ()),
        harness=HarnessRecord.model_validate(
            {k: trial[k] for k in ("valid", "invalid_reasons", "observations") if k in trial}
        )
        if isinstance(trial, dict)
        else None,
    )


def write_outputs(directory: Path, output: CheckerOutput) -> None:
    """result.json (when any invariant was exercised) and checker.json, in canonical bytes."""
    result = output.result_json()
    if result is not None:
        (directory / "result.json").write_bytes(result.encode("utf-8"))
    (directory / "checker.json").write_bytes(output.report_json().encode("utf-8"))
