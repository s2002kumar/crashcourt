"""Development runs of the instrument cells' shapes (C3 on W1 and W5, K1a, K3), judged by the
frozen checker and compared with the draft pre-registration's predictions for the cells of the
same shape. Not the pre-registered batches: those run only through the gate, from their own
scenario files, once the pre-registration is final.

    python research/evidence/t-11/shapes.py --trials 20 --out <scratch dir> --report <path>.json

Linux only, from the WSL clone or CI, like `crashcourt run`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from crashcourt.checker.engine import check_trial  # noqa: E402
from crashcourt.checker.load import load_trial, write_outputs  # noqa: E402
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256  # noqa: E402
from crashcourt.experiment.verify import TrialFiles  # noqa: E402
from crashcourt.orchestrator.declared import trial_scenario  # noqa: E402
from crashcourt.orchestrator.runner import RunOptions, Settings, run_batch  # noqa: E402
from crashcourt.spec.scenario import load_scenario, scenario_sha256  # noqa: E402
from tests.integration.test_instrument_controls import as_predicted  # noqa: E402

SHAPES: dict[str, tuple[str, str, str | None]] = {
    # shape: (pre-registered cell of the same shape, evidence class, published result)
    "c3-w1": ("C3-w1-sync", "control", None),
    "c3-w5": ("C3-w5-sync", "control", None),
    "k1a-shape": ("K1a", "replication", "langgraph#8039"),
    "k3-shape": ("K3", "replication", "langgraph#8796"),
}


def run_shape(name: str, trials: int, out: Path, stamp: str) -> dict[str, Any]:
    cell, evidence, published = SHAPES[name]
    path = ROOT / "tests" / "integration" / "scenarios" / f"t11-{name}.yaml"
    scenario = load_scenario(path)
    summary = run_batch(
        scenario,
        RunOptions(trials=trials, out=out, batch=f"t11-{name}-{stamp}"),
        Settings.from_environment(ROOT),
        source=path,
    )
    registry = ClauseRegistry.load(ROOT / "clauses")
    registry_hash = registry_sha256(ROOT / "clauses")
    outcomes: dict[str, Counter[str]] = {}
    differs: dict[str, list[str]] = {}
    invalid: dict[str, str] = {}
    for trial in summary.trials:
        key = str(trial.number)
        if not trial.valid:
            invalid[key] = "; ".join(trial.reasons)
            continue
        facts = trial_scenario(
            summary.directory,
            trial.number,
            evidence=evidence,  # type: ignore[arg-type]
            replication_of=published,
        )
        output = check_trial(
            load_trial(trial.directory, facts), registry, registry_sha256=registry_hash
        )
        write_outputs(trial.directory, output)
        if output.report.invalid is not None:
            invalid[key] = (
                f"trigger {output.report.invalid.trigger}: {output.report.invalid.reason}"
            )
            continue
        result = output.result_json()
        files = TrialFiles(
            trial.number,
            trial.directory,
            json.loads(output.report_json()),
            json.loads(result) if result is not None else None,
        )
        for r in (files.result or {}).get("results", []):
            outcomes.setdefault(r["invariant"], Counter())[r["outcome"]] += 1
        if found := as_predicted(cell, files):
            differs[key] = found
    valid = len(summary.trials) - len(invalid)
    return {
        "shape": name,
        "preregistered_cell": cell,
        "scenario_sha256": scenario_sha256(scenario),
        "batch": summary.batch,
        "trials": len(summary.trials),
        "valid": valid,
        "as_predicted": valid - len(differs),
        "invalid": invalid,
        "differs": differs,
        "outcomes": {k: dict(sorted(v.items())) for k, v in sorted(outcomes.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    found = [run_shape(name, args.trials, args.out, stamp) for name in SHAPES]
    manifest = json.loads((args.out / found[0]["batch"] / "manifest.json").read_text())
    report = {
        "v": 1,
        "note": "development runs of instrument shapes; not pre-registered batches",
        "git_sha": manifest["git_sha"],
        "git_dirty": manifest["git_dirty"],
        "shapes": found,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for s in found:
        print(
            f"{s['shape']}: {s['as_predicted']} of {s['trials']} as predicted for "
            f"{s['preregistered_cell']}, {len(s['invalid'])} invalid"
        )
    return 0 if all(s["as_predicted"] == s["trials"] for s in found) else 1


if __name__ == "__main__":
    sys.exit(main())
