"""Control C5, oracle sensitivity, as the first matrix's methodology states it
(crashcourt.experiment.controls.C5_METHOD).

    python -m tests.mutants.c5 --trials 20 --out <scratch dir> --report <path>.json

Each applicable mutant in tests/mutants/savers.py runs one ordinary batch of its pre-registered
shape (sync, one worker, no fault: C1's shape; W1 approve for M3 and M4, W5 sequential for M5) with
the mutant in place of the pinned saver, through the same runner as every batch. Every trial is
judged with the frozen checker, whose outputs are written into the trial directory as for any
trial. A trial catches its mutant when the checker made it valid and found an anomaly (EXPECTED,
GAP, or VIOLATION_CANDIDATE) on the mutant's intended invariant. The threshold is 19 of 20.

M1 and M2 are not run: no first-matrix workload exercises them. The report names them as NOT
EXERCISED with the methodology's reasons, and its coverage line says how many of the specified
mutants were measured, so three are never read as five of five.

The report (crashcourt.experiment.controls.ControlReport) records every count, the target's
outcome in every trial, every INVALID reason, every other invariant that showed an anomaly, the
checker it ran under, the manifest, and the sha256 of the mutants, this driver, and the scenarios,
so that the gate can tell whether the report describes the committed files. It is written whatever
the result; `applicable_passed` is computed from the counts. Linux only, from a clean checkout,
against the compose stack, like `crashcourt run`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from crashcourt.checker.engine import CheckerOutput, check_trial
from crashcourt.checker.freeze import FROZEN_PATH, verify_checker
from crashcourt.checker.load import load_trial, write_outputs
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256
from crashcourt.experiment import gitproof
from crashcourt.experiment.controls import (
    C5_CAUGHT,
    C5_METHOD,
    C5_REQUIREMENT,
    C5_TRIALS,
    NOT_EXERCISED,
    Applicable,
    CheckerUsed,
    ControlReport,
    Exclusion,
    MutantResult,
    Threshold,
    recount,
    required_sources,
)
from crashcourt.experiment.fingerprint import unexplained
from crashcourt.experiment.observe import ANOMALY
from crashcourt.manifest import Manifest
from crashcourt.orchestrator.declared import trial_scenario
from crashcourt.orchestrator.runner import BatchSummary, RunOptions, Settings, run_batch
from crashcourt.spec.scenario import load_scenario, scenario_sha256
from tests.mutants.savers import CATALOG, Mutant

ROOT = Path(__file__).resolve().parents[2]
DEFECTS = {m.id: m.defect for m in CATALOG}


class C5Refused(Exception):
    """C5 cannot produce a report that would describe the committed files."""


def outcomes(output: CheckerOutput) -> dict[str, str]:
    """Each invariant's outcome in the trial's result record."""
    text = output.result_json()
    results: list[dict[str, Any]] = json.loads(text)["results"] if text else []
    return {str(r["invariant"]): str(r["outcome"]) for r in results}


def judge_batch(
    mutant: Mutant,
    scenario: str,
    summary: BatchSummary,
    registry: ClauseRegistry,
    registry_hash: str,
) -> MutantResult:
    """Every trial of one mutant's batch through the frozen checker."""
    target: Counter[str] = Counter()
    others: Counter[str] = Counter()
    invalid: dict[str, str] = {}
    caught = valid = 0
    for trial in summary.trials:
        facts = trial_scenario(summary.directory, trial.number, evidence="control")
        output = check_trial(
            load_trial(trial.directory, facts), registry, registry_sha256=registry_hash
        )
        write_outputs(trial.directory, output)
        found = outcomes(output)
        if output.report.invalid is not None:
            invalid[str(trial.number)] = (
                f"trigger {output.report.invalid.trigger}: {output.report.invalid.reason}"
            )
            target["INVALID"] += 1
            continue
        valid += 1
        value = found.get(mutant.target, "NOT_EXERCISED")
        target[value] += 1
        caught += value in ANOMALY
        others.update(k for k, v in found.items() if v in ANOMALY and k != mutant.target)
    raw = (summary.directory / "manifest.json").read_bytes()
    return MutantResult(
        id=mutant.id,
        defect=mutant.defect,
        target=mutant.target,
        scenario=scenario,
        batch=summary.batch,
        scenario_sha256=scenario_sha256(load_scenario(ROOT / scenario)),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        trials=len(summary.trials),
        valid=valid,
        caught=caught,
        target_outcomes=dict(sorted(target.items())),
        invalid=invalid,
        other_anomalies=dict(sorted(others.items())),
    )


def sources(root: Path) -> dict[str, str]:
    """sha256 of each source as committed at HEAD; refused when the working copy differs."""
    head = gitproof.head(root)
    found: dict[str, str] = {}
    for path in required_sources():
        committed = gitproof.blob_sha256(root, head, path)
        if committed is None:
            raise C5Refused(f"{path} is not committed")
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != committed:
            raise C5Refused(f"{path} differs from HEAD; C5 runs only what is committed")
        found[path] = committed
    return found


def mutant_for(spec: Applicable) -> Mutant:
    """The catalog's mutant, held to the methodology's intended invariant."""
    mutant = next(m for m in CATALOG if m.id == spec.id)
    if mutant.target != spec.target:
        raise C5Refused(f"{spec.id} targets {mutant.target}; the methodology says {spec.target}")
    return mutant


def run_c5(
    trials: int, out: Path, *, stamp: str, only: Sequence[str] | None = None
) -> ControlReport:
    settings = Settings.from_environment(ROOT)
    registry = ClauseRegistry.load(ROOT / "clauses")
    registry_hash = registry_sha256(ROOT / "clauses")
    frozen_md = FROZEN_PATH.read_bytes()
    frozen = verify_checker(frozen_md.decode("utf-8"), registry_hash)
    files = sources(ROOT)
    results: list[MutantResult] = []
    manifests: list[Manifest] = []
    for spec in C5_METHOD.applicable:
        if only is not None and spec.id not in only:
            continue
        mutant = mutant_for(spec)
        options = RunOptions(
            trials=trials, out=out, batch=f"c5-{spec.id}-{stamp}", saver_mutant=spec.id
        )
        path = ROOT / spec.scenario
        summary = run_batch(load_scenario(path), options, settings, source=path)
        raw = (summary.directory / "manifest.json").read_text(encoding="utf-8")
        manifests.append(Manifest.from_json(raw))
        results.append(judge_batch(mutant, spec.scenario, summary, registry, registry_hash))
        print(
            f"{spec.id} on {spec.scenario}: caught by {spec.target} in {results[-1].caught} "
            f"of {trials}",
            flush=True,
        )
    for other in manifests[1:]:
        differing = unexplained(manifests[0], other, ())
        if differing:
            raise C5Refused(
                f"the mutant batches ran in different environments: {sorted(differing)}"
            )
    if manifests[0].git_dirty:
        raise C5Refused("the batches ran from a dirty checkout")
    draft = ControlReport(
        control="C5",
        requirement=C5_REQUIREMENT,
        threshold=Threshold(caught=C5_CAUGHT, trials=C5_TRIALS),
        specified=C5_METHOD.specified,
        applicable=len(C5_METHOD.applicable),
        coverage=C5_METHOD.coverage(),
        checker=CheckerUsed(
            version=frozen.checker_version,
            checker_sha256=frozen.checker_sha256,
            frozen_md_sha256=hashlib.sha256(frozen_md).hexdigest(),
            registry_sha256=frozen.registry_sha256,
        ),
        run_commit=manifests[0].git_sha,
        sources=files,
        manifest=json.loads(manifests[0].to_json()),
        mutants=tuple(results),
        excluded=tuple(
            Exclusion(
                id=e.id,
                defect=DEFECTS[e.id],
                target=e.target,
                status="NOT_EXERCISED",
                reason=e.reason,
            )
            for e in C5_METHOD.excluded
        ),
        applicable_passed=False,
    )
    return draft.model_copy(update={"applicable_passed": not recount(draft)})


def summary_text(report: ControlReport) -> str:
    result = "PASS" if report.applicable_passed else "FAIL"
    lines = [
        f"# C5: saver mutants under checker {report.checker.version}",
        "",
        f"Coverage: {report.coverage}.",
        "",
        f"Applicable mutants, threshold {report.threshold.caught} of {report.threshold.trials}: "
        f"{result}.",
        "",
        "| Mutant | Defect | Shape | Target | Caught | Valid | Target's outcomes "
        "| Other anomalies |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in report.mutants:
        shown = ", ".join(f"{k} {v}" for k, v in m.target_outcomes.items())
        others = ", ".join(f"{k} {v}" for k, v in m.other_anomalies.items()) or "none"
        lines.append(
            f"| {m.id} | {m.defect} | {m.scenario} | {m.target} | {m.caught} of {m.trials} | "
            f"{m.valid} | {shown} | {others} |"
        )
    lines += [
        "",
        f"Not exercised by the first matrix (never run here, never PASS): {NOT_EXERCISED}.",
        "",
        "| Mutant | Defect | Declared target | Status | Why |",
        "| --- | --- | --- | --- | --- |",
        *(
            f"| {e.id} | {e.defect} | {e.target} | {e.status} | {e.reason} |"
            for e in report.excluded
        ),
    ]
    failures = recount(report)
    if failures:
        lines += ["", *(f"- {f}" for f in failures)]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.mutants.c5")
    parser.add_argument("--trials", type=int, default=C5_TRIALS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=None, help="mutant ids (development only)")
    args = parser.parse_args(argv)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        report = run_c5(args.trials, args.out, stamp=stamp, only=args.only)
    except C5Refused as exc:
        print(f"C5 refused: {exc}", file=sys.stderr)
        return 2
    args.report.write_bytes(report.to_json().encode("utf-8"))
    args.report.with_suffix(".md").write_bytes(summary_text(report).encode("utf-8"))
    print(summary_text(report))
    return 0 if report.applicable_passed else 1


if __name__ == "__main__":
    sys.exit(main())
