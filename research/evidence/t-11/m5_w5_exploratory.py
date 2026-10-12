"""EXPLORATORY, NOT C5. Mutant M5 (stale head) in W5's sequential C1 shape, which posts no effects.

C5's original shape for every mutant was W1 approve (issue #30), and there every M5 trial is
INVALID by checker v1's I3 alarm (trigger 15) before I6b is judged. This run asked only whether v1
can see a stale head through I6b when the alarm cannot fire; it did not count toward C5. Its result
is kept as it was (m5-w5-exploratory.json). The revised C5 now runs M5 on W5 itself
(tests/mutants/c5.py).

    python research/evidence/t-11/m5_w5_exploratory.py --trials 20 --out <dir> --report <path>.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from crashcourt.clauses.registry import ClauseRegistry, registry_sha256  # noqa: E402
from crashcourt.orchestrator.runner import RunOptions, Settings, run_batch  # noqa: E402
from crashcourt.spec.scenario import load_scenario  # noqa: E402
from tests.mutants import c5  # noqa: E402
from tests.mutants.savers import CATALOG  # noqa: E402

SCENARIO = ROOT / "scenarios" / "w5-sequential.yaml"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    summary = run_batch(
        load_scenario(SCENARIO),
        RunOptions(trials=args.trials, out=args.out, batch=f"m5-w5-{stamp}", saver_mutant="M5"),
        Settings.from_environment(ROOT),
        source=SCENARIO,
    )
    result = c5.judge_batch(
        next(m for m in CATALOG if m.id == "M5"),
        "scenarios/w5-sequential.yaml",
        summary,
        ClauseRegistry.load(ROOT / "clauses"),
        registry_sha256(ROOT / "clauses"),
    )
    manifest = json.loads((summary.directory / "manifest.json").read_text(encoding="utf-8"))
    data = {
        "exploratory": True,
        "counts_toward_c5": False,
        "scenario": "scenarios/w5-sequential.yaml",
        "git_sha": manifest["git_sha"],
        "git_dirty": manifest["git_dirty"],
        "result": json.loads(result.model_dump_json()),
    }
    args.report.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data["result"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
