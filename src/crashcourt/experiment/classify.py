"""Classifying a batch's trials with the frozen checker: the step between `crashcourt run` and
`crashcourt experiment verify`.

`classify_batch(batch_dir, repo)` builds every trial's facts from the batch's own records (the
pinned TrialScenario builder, crashcourt.orchestrator.declared), runs the checker on its histories,
and writes checker.json and, when anything was exercised, result.json into the trial directory.
Trials are the runner's, 1 to N, each in `<cell>/trial-NNNNN/`.

What the trials are declared as comes from the batch, never from the caller. A batch the gate
authorized is classified as its cell's evidence class, with the published result it replicates and
its cell id, from the pre-registration at the commit the authorization names; and only from a clean
checkout of the authorized run commit, so the checker and the builder are the ones the batch ran
under. A batch with no authorization is a development run, classified as a control. A batch whose
authorization the runner revoked is refused.

A verdict is never replaced: a trial that already has checker.json or result.json refuses the whole
batch before anything is written. `compare=True` classifies again without writing and reports every
file whose bytes differ from the recorded ones: the checker run twice on the same histories must
give byte-identical output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, get_args

from crashcourt.checker.engine import check_trial
from crashcourt.checker.load import load_trial, write_outputs
from crashcourt.checker.status import EvidenceClass
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256
from crashcourt.experiment import gitproof
from crashcourt.experiment.gate import AUTHORIZATION_FILE, PREREG, REVOKED_FILE, Authorization
from crashcourt.experiment.preregistration import parse
from crashcourt.orchestrator.declared import trial_scenario

OUTPUTS = ("checker.json", "result.json")


class NotClassified(Exception):
    """The batch cannot be classified as declared; nothing was written."""


@dataclass(frozen=True)
class Declared:
    """What the batch's trials are classified as."""

    evidence: EvidenceClass
    replication_of: str | None
    matrix_cell: str | None


@dataclass(frozen=True)
class Classified:
    trial: int
    #: The trial's INVALID trigger and reason, or None for a valid trial.
    invalid: str | None
    #: Each invariant's outcome, from result.json.
    outcomes: dict[str, str]
    #: With compare: each output whose bytes differ from the recorded file.
    differs: tuple[str, ...] = ()


def declared(batch_dir: Path, repo: Path) -> Declared:
    """The evidence class the batch's own records declare its trials to be."""
    if (batch_dir / REVOKED_FILE).exists():
        raise NotClassified(f"{batch_dir}: the runner revoked this batch's authorization")
    path = batch_dir / AUTHORIZATION_FILE
    if not path.exists():
        return Declared("control", None, None)
    auth = Authorization.model_validate_json(path.read_text(encoding="utf-8"))
    head, changed = gitproof.head(repo), gitproof.dirty(repo)
    if head != auth.run_commit or changed:
        raise NotClassified(
            f"{batch_dir}: an authorized batch is classified from a clean checkout of its run "
            f"commit {auth.run_commit}, not {head}{' with changes' if changed else ''}"
        )
    text = gitproof.blob(repo, auth.preregistration_commit, PREREG)
    if text is None:
        raise NotClassified(f"{PREREG} is not in commit {auth.preregistration_commit}")
    cell = parse(text.decode("utf-8")).cell(auth.cell)
    if auth.evidence not in get_args(EvidenceClass):
        raise NotClassified(f"{batch_dir}: the authorization names evidence {auth.evidence!r}")
    return Declared(cast(EvidenceClass, auth.evidence), cell.replication_of, cell.id)


def _trials(batch_dir: Path) -> tuple[str, list[int]]:
    run: dict[str, Any] = json.loads((batch_dir / "run.json").read_text(encoding="utf-8"))
    cell, trials = run.get("cell"), run.get("trials")
    if not isinstance(cell, str) or not isinstance(trials, int) or trials < 1:
        raise NotClassified(f"{batch_dir}: run.json names no cell and trial count")
    return cell, list(range(1, trials + 1))


def classify_batch(batch_dir: Path, repo: Path, *, compare: bool = False) -> list[Classified]:
    """Every trial of the batch classified by the frozen checker; NotClassified otherwise."""
    how = declared(batch_dir, repo)
    cell, numbers = _trials(batch_dir)
    directories = {n: batch_dir / cell / f"trial-{n:05d}" for n in numbers}
    missing = [n for n, d in directories.items() if not d.is_dir()]
    if missing:
        raise NotClassified(f"{batch_dir}: no directory for trials {missing}")
    if not compare:
        written = sorted(
            f"trial {n} {name}"
            for n, d in directories.items()
            for name in OUTPUTS
            if (d / name).exists()
        )
        if written:
            raise NotClassified(f"{batch_dir}: already classified ({', '.join(written)})")
    registry = ClauseRegistry.load(repo / "clauses")
    digest = registry_sha256(repo / "clauses")
    found: list[Classified] = []
    for n, directory in directories.items():
        facts = trial_scenario(
            batch_dir,
            n,
            evidence=how.evidence,
            replication_of=how.replication_of,
            matrix_cell=how.matrix_cell,
        )
        output = check_trial(load_trial(directory, facts), registry, registry_sha256=digest)
        outputs = {"checker.json": output.report_json(), "result.json": output.result_json()}
        differs: tuple[str, ...] = ()
        if compare:
            differs = tuple(
                name
                for name, text in outputs.items()
                if _recorded(directory / name) != (None if text is None else text.encode())
            )
        else:
            write_outputs(directory, output)
        invalid = output.report.invalid
        result = json.loads(outputs["result.json"] or "{}")
        found.append(
            Classified(
                n,
                None if invalid is None else f"trigger {invalid.trigger}: {invalid.reason}",
                {r["invariant"]: r["outcome"] for r in result.get("results", [])},
                differs,
            )
        )
    return found


def _recorded(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None
