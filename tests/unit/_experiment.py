"""Builders for the experiment-control tests: a real git repository holding a final
pre-registration, a frozen checker, and a passing C5 controls report, stand-ins for the T-10
checker and the T-07 scenario loader, and batch directories shaped as the runner and the checker
write them."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crashcourt.experiment.controls import (
    C5_CAUGHT,
    C5_METHOD,
    C5_REQUIREMENT,
    C5_TRIALS,
    CheckerUsed,
    ControlReport,
    Exclusion,
    MutantResult,
    Threshold,
)
from crashcourt.experiment.gate import (
    AUTHORIZATION_FILE,
    FROZEN_PATH,
    Authorization,
    FrozenChecker,
    ScenarioFacts,
    Unavailable,
)
from crashcourt.experiment.preregistration import (
    BUILDER_PATH,
    PREREGISTRATION_PATH,
    Preregistration,
    block,
    derive_seed,
)
from crashcourt.manifest import Manifest
from crashcourt.spec.records import BatchRecord, ResultRecord

from ._factories import make_manifest

CHECKER_SHA = "c" * 64
REGISTRY_SHA = "e" * 64
LANDMARK_SHA = "d" * 64
FROZEN_MD = "# Checker v1: frozen\n\nChecker hash `" + CHECKER_SHA + "`.\n"
FROZEN_MD_SHA = hashlib.sha256(FROZEN_MD.encode()).hexdigest()
EFFECT_MODELS = {"w1": "a" * 64, "w5": "b" * 64}
LANDMARK_TABLES = {"langgraph-v1": LANDMARK_SHA}
#: C3 is a placebo control some tests add to the matrix; the default pre-registration has R1 and X1.
SEEDS = {"R1": "seed-r1", "X1": "seed-x1", "C3": "seed-c3"}
#: Each cell's scenario in canonical form, as the runner writes scenario.json, and its hash.
SCENARIO_JSON = {
    c: json.dumps({"cell": c, "seed": s}, separators=(",", ":")) for c, s in SEEDS.items()
}
SCENARIOS = {c: hashlib.sha256(text.encode()).hexdigest() for c, text in SCENARIO_JSON.items()}
N = 3
#: The TrialScenario builder as the fixture repository commits it, and its pinned hash.
BUILDER_TEXT = '"""The TrialScenario builder, as pre-registered."""\n'
BUILDER_SHA = hashlib.sha256(BUILDER_TEXT.encode()).hexdigest()
#: The C5 controls report the pre-registration's C5 instrument check names, and the files it ran.
C5_REPORT = "research/evidence/c5.json"
C5_SOURCES = {
    "tests/mutants/savers.py": "labeled mutants\n",
    "tests/mutants/c5.py": "driver\n",
    "scenarios/w1-approve.yaml": "w1 approve\n",
    "scenarios/w5-sequential.yaml": "w5 sequential\n",
}
#: The effect ledger's export as the post-trial step writes it: the replication's published
#: shape, two charge rows with attempts 1 and 2.
LEDGER: dict[str, Any] = {
    "rows": [
        {"effect_key": "charge:t1", "attempt": 1, "node": "execute"},
        {"effect_key": "charge:t1", "attempt": 2, "node": "execute"},
    ],
    "verify_ok": True,
}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout.decode().strip()


def commit(repo: Path, files: Mapping[str, str], message: str = "change") -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def prereg_model(**changes: Any) -> Preregistration:
    data: dict[str, Any] = {
        "v": 1,
        "name": "test-matrix",
        "state": "final",
        "checker": {
            "version": "v1",
            "checker_sha256": CHECKER_SHA,
            "frozen_md_sha256": FROZEN_MD_SHA,
            "registry_sha256": REGISTRY_SHA,
            "effect_models": EFFECT_MODELS,
            "landmark_tables": LANDMARK_TABLES,
        },
        "builder": {"path": BUILDER_PATH, "sha256": BUILDER_SHA},
        "environment": {"os_system": "Linux", "runner": "local", "git_dirty": False},
        "instrument_checks": [
            {"id": "AM", "requirement": "mutants", "evidence": "ci run 1"},
            {"id": "C5", "requirement": C5_REQUIREMENT, "evidence": C5_REPORT},
        ],
        "cells": [
            {
                "id": "R1",
                "phase": "instrument",
                "evidence": "replication",
                "replication_of": "langgraph#8039",
                "workload": "w1",
                "durability": "sync",
                "fault": "F01",
                "workers": 1,
                "trigger": "kill before the writes commit",
                "scenario": "scenarios/R1.yaml",
                "scenario_sha256": SCENARIOS["R1"],
                "master_seed": SEEDS["R1"],
                "n": N,
                "confidence": "predicted",
                "predicted": [
                    {"invariant": "I4", "expect": ["EXPECTED"]},
                    {"invariant": "I1b", "expect": ["PASS"]},
                ],
                "observations": [
                    {"source": "ledger", "effect": "charge", "rows": 2, "attempts": [1, 2]}
                ],
                "escalate_if": "anything but as predicted",
            },
            {
                "id": "X1",
                "phase": "new-evidence",
                "evidence": "new-evidence",
                "workload": "w5",
                "durability": "sync",
                "fault": "F06",
                "workers": 2,
                "trigger": "both held after get_tuple",
                "scenario": "scenarios/X1.yaml",
                "scenario_sha256": SCENARIOS["X1"],
                "master_seed": SEEDS["X1"],
                "n": N,
                "confidence": "predicted",
                "predicted": [{"invariant": "I5a", "expect": ["GAP"]}],
                "escalate_if": "UNSTABLE",
            },
        ],
    }
    data.update(changes)
    return Preregistration.model_validate(data)


def c5_report(caught: dict[str, int] | None = None, **changes: Any) -> ControlReport:
    """A C5 report under the test checker and the first matrix's methodology: every applicable
    mutant caught in all 20 trials unless `caught` says otherwise, M1 and M2 not exercised."""
    applicable = C5_METHOD.applicable
    counts = {a.id: C5_TRIALS for a in applicable} | (caught or {})
    data: dict[str, Any] = {
        "control": "C5",
        "requirement": C5_REQUIREMENT,
        "threshold": Threshold(caught=C5_CAUGHT, trials=C5_TRIALS),
        "specified": C5_METHOD.specified,
        "applicable": len(applicable),
        "coverage": C5_METHOD.coverage(),
        "checker": CheckerUsed(
            version="v1",
            checker_sha256=CHECKER_SHA,
            frozen_md_sha256=FROZEN_MD_SHA,
            registry_sha256=REGISTRY_SHA,
        ),
        "run_commit": "1" * 40,
        "sources": {
            path: hashlib.sha256(text.encode()).hexdigest() for path, text in C5_SOURCES.items()
        },
        "manifest": json.loads(make_manifest(git_sha="1" * 40).to_json()),
        "mutants": tuple(
            MutantResult(
                id=a.id,
                defect="a defect",
                target=a.target,
                scenario=a.scenario,
                batch=f"c5-{a.id}",
                scenario_sha256="5" * 64,
                manifest_sha256="6" * 64,
                trials=C5_TRIALS,
                valid=C5_TRIALS,
                caught=counts[a.id],
                target_outcomes={"GAP": counts[a.id], "PASS": C5_TRIALS - counts[a.id]},
            )
            for a in applicable
        ),
        "excluded": tuple(
            Exclusion(
                id=e.id,
                defect="a defect",
                target=e.target,
                status="NOT_EXERCISED",
                reason=e.reason,
            )
            for e in C5_METHOD.excluded
        ),
        "applicable_passed": all(n >= C5_CAUGHT for n in counts.values()),
    }
    data.update(changes)
    return ControlReport.model_validate(data)


def prereg_text(prereg: Preregistration) -> str:
    return f"# Pre-registration\n\nTest matrix.\n\n{block(prereg)}\n"


@dataclass
class Repo:
    root: Path
    freeze_commit: str
    prereg_commit: str


def make_repo(
    tmp: Path, prereg: Preregistration | None = None, report: ControlReport | None = None
) -> Repo:
    root = tmp / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, {".gitignore": "runs/\n", "README.md": "test\n"}, "init")
    freeze = commit(root, {str(FROZEN_PATH): FROZEN_MD}, "freeze the checker")
    files = {f"scenarios/{c}.yaml": f"cell: {c}\n" for c in SCENARIOS}
    files[BUILDER_PATH] = BUILDER_TEXT
    files[str(PREREGISTRATION_PATH)] = prereg_text(prereg or prereg_model())
    files[C5_REPORT] = (report or c5_report()).to_json()
    files.update(C5_SOURCES)
    prereg_commit = commit(root, files, "pre-register the matrix")
    return Repo(root, freeze, prereg_commit)


@dataclass
class FakeChecker:
    """The T-10 checker as the gate sees it."""

    state: str = "FROZEN"
    frozen: FrozenChecker = field(
        default_factory=lambda: FrozenChecker(
            "v1", CHECKER_SHA, REGISTRY_SHA, dict(EFFECT_MODELS), dict(LANDMARK_TABLES)
        )
    )
    mismatch: str | None = None
    landmark: str | Exception | None = LANDMARK_SHA
    seen: list[str] = field(default_factory=list[str])

    def freeze_state(self) -> str:
        return self.state

    def registry_sha256(self, root: Path) -> str:
        return REGISTRY_SHA

    def verify_checker(self, frozen_md: str, registry_sha256: str) -> FrozenChecker:
        self.seen.append(frozen_md)
        if self.mismatch is not None:
            raise ValueError(self.mismatch)
        return self.frozen

    def landmark_sha256(self, frozen_md: str, registry_sha256: str, scenario: Path) -> str | None:
        if isinstance(self.landmark, Exception):
            raise self.landmark
        return self.landmark


@dataclass
class FakeScenarios:
    """T-07's scenario loader as the gate sees it: facts by cell file name."""

    facts: dict[str, ScenarioFacts] = field(
        default_factory=lambda: {c: ScenarioFacts(SCENARIOS[c], c, SEEDS[c]) for c in SCENARIOS}
    )
    unavailable: bool = False

    def load(self, path: Path) -> ScenarioFacts:
        if self.unavailable:
            raise Unavailable("crashcourt.spec.scenario (T-07) is not in this checkout")
        return self.facts[path.stem]


def manifest_for(run_commit: str, **changes: Any) -> Manifest:
    return make_manifest(git_sha=run_commit, **changes)


def outcome(invariant: str, name: str) -> dict[str, Any]:
    out: dict[str, Any] = {"invariant": invariant, "outcome": name}
    if name in ("PASS", "EXPECTED", "VIOLATION_CANDIDATE"):
        out["clause"] = "LG-TEST"
    if name in ("EXPECTED", "GAP", "VIOLATION_CANDIDATE"):
        out["evidence"] = ["trial/merged.jsonl#/1"]
    if name == "INVALID":
        out["reason"] = "fault not fired"
    return out


def make_batch(
    out: Path,
    batch: str,
    auth: Authorization,
    manifest: Manifest,
    trials: Sequence[Sequence[tuple[str, str]] | None],
    *,
    checker_changes: Mapping[str, Any] | None = None,
    invalid: Mapping[int, str] | None = None,
    run_changes: Mapping[str, Any] | None = None,
    scenario_text: str | None = None,
    trial_checker: Mapping[int, Mapping[str, Any]] | None = None,
    ledgers: Mapping[int, Any] | None = None,
) -> Path:
    """A batch directory: records at the root, and one trial directory per entry of `trials`
    (its (invariant, outcome) pairs, or None for a trial in which nothing was exercised).
    `trial_checker` changes one trial's checker.json; `ledgers` replaces one trial's ledger.json
    (None leaves it out)."""
    root = out / batch
    root.mkdir(parents=True)
    manifest_bytes = manifest.to_json().encode()
    (root / "manifest.json").write_bytes(manifest_bytes)
    record = BatchRecord(
        v=1,
        batch=batch,
        provenance="run",
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        scenario_sha256=auth.scenario_sha256,
    )
    (root / "batch.json").write_text(record.model_dump_json(), encoding="utf-8")
    (root / AUTHORIZATION_FILE).write_text(auth.to_json(), encoding="utf-8")
    scenario = scenario_text if scenario_text is not None else SCENARIO_JSON[auth.cell]
    (root / "scenario.json").write_bytes((scenario + "\n").encode())
    run: dict[str, Any] = {
        "v": 1,
        "batch": batch,
        "cell": auth.cell,
        "mode": "canonical",
        "trials": len(trials),
        "master_seed": auth.master_seed,
        "trial_seeds": {
            str(n): derive_seed(auth.master_seed, auth.cell, n) for n in range(1, len(trials) + 1)
        },
    }
    run.update(run_changes or {})
    (root / "run.json").write_text(json.dumps(run), encoding="utf-8")
    for n, pairs in enumerate(trials, start=1):  # the runner numbers trials 1..N
        trial = root / auth.cell / f"trial-{n:05d}"
        trial.mkdir(parents=True)
        report: dict[str, Any] = {
            "checker_version": auth.checker_version,
            "freeze_state": "FROZEN",
            "authorized_for_new_evidence": True,
            "frozen_sha256": auth.checker_sha256,
            "checker_sha256": auth.checker_sha256,
            "registry_sha256": auth.registry_sha256,
            "effect_model_sha256": "f" * 64,
            "landmark_table_sha256": auth.landmark_table_sha256,
            "batch": batch,
            "cell": auth.cell,
            "trial": n,
            "invalid": None,
            "not_exercised": [],
        }
        if invalid and n in invalid:
            report["invalid"] = {"trigger": 10, "name": invalid[n], "reason": invalid[n]}
        if pairs is None:
            report["not_exercised"] = [{"invariant": "I5a", "missing": "no fork window"}]
        report.update(checker_changes or {})
        report.update((trial_checker or {}).get(n, {}))
        (trial / "checker.json").write_text(json.dumps(report), encoding="utf-8")
        ledger = (ledgers or {}).get(n, LEDGER)
        if ledger is not None:
            (trial / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        if pairs is not None:
            result = ResultRecord.model_validate(
                {
                    "v": 1,
                    "batch": batch,
                    "cell": auth.cell,
                    "trial": n,
                    "provenance": "run",
                    "manifest_sha256": record.manifest_sha256,
                    "results": [outcome(i, o) for i, o in pairs],
                }
            )
            (trial / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    return root
