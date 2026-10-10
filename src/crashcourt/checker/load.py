"""Read a trial directory into the checker's inputs, and write its outputs beside them.

Layout (T-07's runner, plus the three files its post-trial step adds):

| File | Read as |
| --- | --- |
| `<batch>/manifest.json` | `manifest`, and the sha256 of its bytes |
| `<trial>/<proc>.jsonl` | `histories`: complete lines, the partial tail, or the parse error |
| `<trial>/trial.json` | `harness`: the orchestrator's valid, invalid_reasons, observations |
| `<trial>/ledger.json` | `ledger`: a LedgerExport |
| `<trial>/snapshot.json` | `snapshots`: a SnapshotPair |
| `<trial>/sink-health.json` | `sink_health`: a JSON list of two SinkHealth readings |

The process histories are the ones trial.json lists (all `.jsonl` files when there is no
trial.json); `merged.jsonl` (a display copy) and `db-rows.jsonl` (an export of the saver rows) are
never read as histories.

The post-trial files (frozen with the checker; the runner writes each as the model's
`model_dump_json()`, so its bytes are this schema by construction):

- They are evidence only. Nothing read for them may decide the schedule, a fault, or a retry: the
  pre-trial count and the "before" reading are taken before the first dispatch and recorded,
  everything else after quiescence, when the trial's schedule is over.
- `snapshot.json`: `crashcourt.snapshot.dump.take_pair(dsn, threads, pretrial, ...)` on the
  direct oracle DSN (the manifest's, never a worker's proxied route), for every thread of the
  scenario, with `pretrial` from `crashcourt.snapshot.dump.pretrial_rows(dsn, threads)` read
  before the first dispatch. Quiescence: every worker the trial started has exited and been
  reaped, and every fault is healed; `after_orchestrator_seq` is the orchestrator history's last
  seq at that point; `toxics_installed` names every toxic on any proxy then, as `proxy/toxic`
  (non-empty is INVALID). The two snapshots must be at least two seconds apart by the database
  clock (`take_pair`'s default gap) and hold the same rows.
- `ledger.json`: `LedgerExport.from_sink(rows, verify)` from the sink's GET /ledger and GET
  /ledger/verify for this trial, read after quiescence: every row of the trial's ledger, as the
  sink returns it, and the sink's verdict on the chain. Written for every trial, with no rows
  when the workload posted none.
- `sink-health.json`: `[SinkHealth.from_reading(before, "before"), SinkHealth.from_reading(after,
  "after")]`, two GET /health answers: "before" after the runner opened the trial in the sink and
  before the first dispatch, "after" with the ledger read.

Missing or malformed evidence fails closed. A runner that cannot read the database or the sink, or
gets an answer the helpers refuse, writes no file rather than a partial one, and an absent file is
left absent: the trial is INVALID (trigger 1). A file that does not parse as its model is recorded
in `load_errors`, and the trial is INVALID (trigger 1) too. The loader never fills a gap. The
scenario facts are the caller's: they come from the batch's scenario and the workload's effect
model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

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

#: JSON-lines files in a trial directory that are not process histories.
NOT_HISTORIES = frozenset({"merged", "db-rows"})

_HEALTH = TypeAdapter(tuple[SinkHealth, ...])


def _history(path: Path) -> ProcessHistory:
    proc = path.stem
    if not path.is_file():
        return ProcessHistory(proc=proc, events=(), parse_error=f"{path.name} is missing")
    try:
        read = read_history(path)
    except HistoryError as exc:
        return ProcessHistory(proc=proc, events=(), parse_error=str(exc))
    return ProcessHistory(proc=proc, events=read.events, partial_tail=read.partial_tail)


def _read[M: BaseModel](path: Path, model: type[M], errors: list[str]) -> M | None:
    """The file as `model`, None when it is absent; a file that does not parse is an error."""
    if not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_bytes())
    except ValueError as exc:
        errors.append(f"{path.name}: {exc}")
        return None


def _health(path: Path, errors: list[str]) -> tuple[SinkHealth, ...]:
    if not path.is_file():
        return ()
    try:
        return _HEALTH.validate_json(path.read_bytes())
    except ValueError as exc:
        errors.append(f"{path.name}: {exc}")
        return ()


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
    trial: dict[str, Any] | None = _json(directory / "trial.json")
    # The orchestrator's record names the histories; without it, every .jsonl but the known
    # non-history files.
    listed: object = trial.get("histories") if isinstance(trial, dict) else None
    if isinstance(listed, dict):
        found = [str(n) for n in cast("dict[object, object]", listed)]
    else:
        found = [p.stem for p in directory.glob("*.jsonl")]
    names = sorted(n for n in found if n not in NOT_HISTORIES)
    histories = tuple(_history(directory / f"{name}.jsonl") for name in names)
    errors: list[str] = []
    ledger = _read(directory / "ledger.json", LedgerExport, errors)
    snapshots = _read(directory / "snapshot.json", SnapshotPair, errors)
    health = _health(directory / "sink-health.json", errors)
    return TrialArtifacts(
        scenario=scenario,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        histories=histories,
        ledger=ledger,
        snapshots=snapshots,
        sink_health=health,
        harness=HarnessRecord.model_validate(
            {k: trial[k] for k in ("valid", "invalid_reasons", "observations") if k in trial}
        )
        if isinstance(trial, dict)
        else None,
        load_errors=tuple(errors),
    )


def write_outputs(directory: Path, output: CheckerOutput) -> None:
    """result.json (when any invariant was exercised) and checker.json, in canonical bytes."""
    result = output.result_json()
    if result is not None:
        (directory / "result.json").write_bytes(result.encode("utf-8"))
    (directory / "checker.json").write_bytes(output.report_json().encode("utf-8"))
