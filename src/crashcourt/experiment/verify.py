"""Re-proving an archived batch: that it ran as pre-registered, under the frozen checker, after both
were committed.

`verify_batch(batch_dir, repo)` reads the batch's authorization.json, manifest.json, and
batch.json, and every trial's checker.json, and checks them against git, not against what the
batch says about itself:

- the manifest names the authorized commit, from a clean checkout;
- the pre-registration commit is an ancestor of that commit, the file did not change between the
  two, and its bytes at the pre-registration commit hash to the recorded value;
- the freeze commit is an ancestor of the pre-registration commit, and FROZEN.md at the run commit
  hashes to the recorded and pre-registered value;
- the batch record's scenario hash, the cell, N, and the checker are the pre-registered ones, and
  the manifest holds the pre-registered environment;
- there are exactly N trials, numbered 1 to N as the runner numbers them, and each trial's
  checker.json was written by the frozen checker (its checker hash, registry, and landmark table
  are the authorized ones), authorized for new evidence.

An empty list of problems is the proof; anything else keeps the batch out of every summary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crashcourt.experiment import gitproof
from crashcourt.experiment.gate import (
    AUTHORIZATION_FILE,
    FROZEN_PATH,
    Authorization,
    confirm_environment,
)
from crashcourt.experiment.preregistration import (
    PREREGISTRATION_PATH,
    Preregistration,
    PreregistrationError,
    parse,
)
from crashcourt.manifest import Manifest
from crashcourt.spec.records import BatchRecord, ResultRecord


@dataclass(frozen=True)
class TrialFiles:
    """One trial's checker output: checker.json, and result.json if anything was exercised."""

    trial: int
    directory: Path
    checker: dict[str, Any]
    result: dict[str, Any] | None


@dataclass(frozen=True)
class VerifiedBatch:
    directory: Path
    batch: BatchRecord
    manifest: Manifest
    authorization: Authorization
    prereg: Preregistration
    trials: tuple[TrialFiles, ...]


class BatchNotVerified(Exception):
    def __init__(self, directory: Path, problems: list[str]) -> None:
        super().__init__(f"{directory}: " + "; ".join(problems))
        self.directory = directory
        self.problems = problems


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_trials(batch_dir: Path) -> list[TrialFiles]:
    """Every trial directory under the batch: those holding a checker.json, in trial order."""
    trials: list[TrialFiles] = []
    for report in sorted(batch_dir.rglob("checker.json")):
        checker = _json(report)
        result_path = report.parent / "result.json"
        result = _json(result_path) if result_path.exists() else None
        trials.append(TrialFiles(int(checker["trial"]), report.parent, checker, result))
    return sorted(trials, key=lambda t: t.trial)


def verify_batch(batch_dir: Path, repo: Path) -> VerifiedBatch:
    """The batch with its pre-registration, if every check holds; BatchNotVerified otherwise."""
    problems: list[str] = []
    try:
        auth = Authorization.model_validate_json(
            (batch_dir / AUTHORIZATION_FILE).read_text("utf-8")
        )
        manifest = Manifest.from_json((batch_dir / "manifest.json").read_text("utf-8"))
        batch = BatchRecord.model_validate_json((batch_dir / "batch.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise BatchNotVerified(batch_dir, [f"unreadable batch records: {exc}"]) from exc

    run, prereg_commit = auth.run_commit, auth.preregistration_commit
    for name, sha in (
        ("run", run),
        ("pre-registration", prereg_commit),
        ("freeze", auth.freeze_commit),
    ):
        if gitproof.resolve(repo, sha) != sha:
            problems.append(f"the {name} commit {sha} is not in this repository")
    if problems:
        raise BatchNotVerified(batch_dir, problems)

    if not gitproof.is_ancestor(repo, prereg_commit, run):
        problems.append(
            f"pre-registration commit {prereg_commit} is not an ancestor of the run {run}"
        )
    if gitproof.last_commit(repo, PREREGISTRATION_PATH.as_posix(), at=run) != prereg_commit:
        problems.append("the pre-registration changed between its commit and the run")
    if (
        gitproof.blob_sha256(repo, prereg_commit, PREREGISTRATION_PATH.as_posix())
        != auth.preregistration_sha256
    ):
        problems.append("the pre-registration's bytes are not the recorded ones")
    if not gitproof.is_ancestor(repo, auth.freeze_commit, prereg_commit):
        problems.append(
            f"freeze commit {auth.freeze_commit} is not an ancestor of the pre-registration"
        )
    if gitproof.blob_sha256(repo, run, FROZEN_PATH) != auth.frozen_md_sha256:
        problems.append(f"{FROZEN_PATH} at the run commit is not the recorded freeze")

    raw = gitproof.blob(repo, prereg_commit, PREREGISTRATION_PATH.as_posix())
    try:
        prereg = parse((raw or b"").decode("utf-8"))
    except PreregistrationError as exc:
        raise BatchNotVerified(batch_dir, [*problems, f"pre-registration: {exc}"]) from exc
    if prereg.state != "final":
        problems.append("the pre-registration was a draft")
    try:
        cell = prereg.cell(auth.cell)
    except KeyError:
        raise BatchNotVerified(
            batch_dir, [*problems, f"cell {auth.cell} is not pre-registered"]
        ) from None

    pin = prereg.checker
    if pin is None or (pin.checker_sha256, pin.frozen_md_sha256, pin.registry_sha256) != (
        auth.checker_sha256,
        auth.frozen_md_sha256,
        auth.registry_sha256,
    ):
        problems.append("the authorized checker is not the pre-registered one")
    for name, got, want in (
        ("scenario_sha256", batch.scenario_sha256, cell.scenario_sha256),
        ("authorized scenario_sha256", auth.scenario_sha256, cell.scenario_sha256),
        ("trials", auth.trials, cell.n),
    ):
        if got != want:
            problems.append(f"{name} is {got}, pre-registered {want}")
    if batch.provenance != "run":
        problems.append(f"batch provenance {batch.provenance}")
    digest = hashlib.sha256((batch_dir / "manifest.json").read_bytes()).hexdigest()
    if digest != batch.manifest_sha256:
        problems.append("batch.json's manifest_sha256 is not the sha256 of manifest.json")
    problems.extend(confirm_environment(prereg, auth, manifest))

    trials = read_trials(batch_dir)
    if sorted(t.trial for t in trials) != list(range(1, cell.n + 1)):
        problems.append(f"trials {[t.trial for t in trials]} are not exactly 1..{cell.n}")
    for t in trials:
        problems.extend(_trial_problems(t, batch, auth))
    if problems:
        raise BatchNotVerified(batch_dir, problems)
    return VerifiedBatch(batch_dir, batch, manifest, auth, prereg, tuple(trials))


def _trial_problems(t: TrialFiles, batch: BatchRecord, auth: Authorization) -> list[str]:
    c = t.checker
    where = f"trial {t.trial}"
    found: list[str] = []
    expected = {
        "batch": batch.batch,
        "cell": auth.cell,
        "checker_version": auth.checker_version,
        "freeze_state": "FROZEN",
        "authorized_for_new_evidence": True,
        "frozen_sha256": auth.checker_sha256,
        "checker_sha256": auth.checker_sha256,
        "registry_sha256": auth.registry_sha256,
        "landmark_table_sha256": auth.landmark_table_sha256,
    }
    for key, want in expected.items():
        if c.get(key) != want:
            found.append(f"{where}: checker.json {key} is {c.get(key)!r}, expected {want!r}")
    if t.result is not None:
        r = t.result
        try:
            ResultRecord.model_validate(r)
        except ValueError as exc:
            found.append(f"{where}: result.json is not a v1 result record: {exc}")
        if (r.get("batch"), r.get("cell"), r.get("trial")) != (batch.batch, auth.cell, t.trial):
            found.append(f"{where}: result.json names another trial")
        if r.get("manifest_sha256") != batch.manifest_sha256 or r.get("provenance") != "run":
            found.append(f"{where}: result.json is not this batch's run record")
    return found
