"""The gate in front of every pre-registered batch: nothing runs unless all of it holds.

`authorize_batch(source, trials, root)` is called by `crashcourt run` before doctor, for a scenario
file the pre-registration lists. It refuses, naming every reason at once, unless:

1. the checkout is clean, and research/preregistration.md is unchanged since the commit that
   last changed it (the pre-registration commit), which is an ancestor of HEAD, and can authorize
   the cell: a final pre-registration any cell, a draft only an instrument cell that is complete
   under complete pins (`Preregistration.refusals`);
2. the checker is FROZEN: src/crashcourt/checker/FROZEN.md exists, its bytes hash to the
   pre-registered value, the checker files, effect models, and clause registry verify against it
   (T-10's `freeze.verify_checker`), its checker hash is the pre-registered one, and the commit
   that last changed it (the freeze commit) is an ancestor of the pre-registration commit; and
   the TrialScenario builder (orchestrator/declared.py), which is outside the freeze, is the file
   the pre-registration pins, at its commit and at HEAD;
3. the scenario is the one pre-registered for its cell: same content hash, same cell id, same
   master seed, and as many workers as the cell names; the landmark table it names is audited
   and frozen (T-10's `freeze.verify_scenario`), and a cell that injects a fault names one; and
   `trials` is the pre-registered N (no optional stopping, no extra trials);
4. for a new-evidence cell, every instrument cell (controls and published replications) of the
   same pre-registration, checker, and environment has batches that came out as predicted.

After doctor writes the manifest, `confirm_environment` checks it against the pre-registered
environment and the authorized commit. The authorization is written into the batch directory
(authorization.json); `crashcourt.experiment.verify` re-checks it against git later.

The checker (T-10) and the scenario loader (T-07) are reached through two small adapters, so the
tests can stand in for them; an adapter that cannot answer raises Unavailable, and the gate refuses.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from crashcourt.checker import freeze, status
from crashcourt.clauses import registry_sha256 as clause_registry_sha256
from crashcourt.experiment import gitproof
from crashcourt.experiment.fingerprint import (
    RULES_PATH,
    CompatibilityRule,
    fingerprint,
    mismatched,
    parse_rules,
    unexplained_between,
)
from crashcourt.experiment.preregistration import (
    PREREGISTRATION_PATH,
    Cell,
    Preregistration,
    PreregistrationError,
    counted,
    parse,
)
from crashcourt.manifest import Manifest
from crashcourt.spec.scenario import load_scenario, scenario_sha256

FROZEN_PATH = PurePosixPath("src/crashcourt/checker/FROZEN.md")
PREREG = PREREGISTRATION_PATH.as_posix()
AUTHORIZATION_FILE = "authorization.json"
#: Where the runner moves the authorization when the checkout changes under a running batch.
REVOKED_FILE = "authorization.revoked.json"


class NotAuthorized(Exception):
    """The batch may not run; `reasons` lists every gate that failed."""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = tuple(reasons)


class Unavailable(Exception):
    """A part of the harness the gate needs is not in this checkout."""


@dataclass(frozen=True)
class FrozenChecker:
    version: str
    checker_sha256: str
    registry_sha256: str
    effect_models: dict[str, str]
    landmark_tables: dict[str, str]


@dataclass(frozen=True)
class _Landmarks:
    sha256: str | None


@dataclass(frozen=True)
class _Freeze:
    commit: str
    text: str
    sha256: str
    checker: FrozenChecker


@dataclass(frozen=True)
class ScenarioFacts:
    sha256: str
    cell: str
    master_seed: str
    #: The worker processes the scenario declares.
    workers: int


class CheckerView(Protocol):
    def freeze_state(self) -> str: ...

    def registry_sha256(self, root: Path) -> str: ...

    def verify_checker(self, frozen_md: str, registry_sha256: str) -> FrozenChecker: ...

    def landmark_sha256(
        self, frozen_md: str, registry_sha256: str, scenario: Path
    ) -> str | None: ...


class ScenarioView(Protocol):
    def load(self, path: Path) -> ScenarioFacts: ...


class T10Checker:
    """The checker as T-10 ships it: checker.status and checker.freeze."""

    def freeze_state(self) -> str:
        return status.FREEZE_STATE

    def registry_sha256(self, root: Path) -> str:
        return clause_registry_sha256(root / "clauses")

    def verify_checker(self, frozen_md: str, registry_sha256: str) -> FrozenChecker:
        frozen = freeze.verify_checker(frozen_md, registry_sha256)
        return FrozenChecker(
            frozen.checker_version,
            frozen.checker_sha256,
            frozen.registry_sha256,
            dict(frozen.effect_models),
            dict(frozen.landmark_tables),
        )

    def landmark_sha256(self, frozen_md: str, registry_sha256: str, scenario: Path) -> str | None:
        """The audited landmark table the scenario names (its `landmarks` key), verified against
        the freeze; None when the scenario names none."""
        return freeze.verify_scenario(frozen_md, registry_sha256, scenario)


class T07Scenarios:
    """Scenarios as T-07 loads them: spec.scenario.load_scenario and scenario_sha256."""

    def load(self, path: Path) -> ScenarioFacts:
        scenario = load_scenario(path)
        return ScenarioFacts(
            scenario_sha256(scenario), scenario.cell, scenario.seed, len(scenario.workers)
        )


class Authorization(BaseModel):
    """authorization.json: why this batch was allowed to run, re-checkable against git."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal[1] = 1
    cell: str
    phase: str
    evidence: str
    scenario: str
    scenario_sha256: str
    master_seed: str
    trials: int
    run_commit: str
    preregistration_commit: str
    preregistration_sha256: str
    #: The pre-registration's state at its commit: a draft authorizes only complete instrument
    #: cells, whose batches never count toward new evidence.
    preregistration_state: Literal["draft", "final"]
    freeze_commit: str
    frozen_md_sha256: str
    checker_version: str
    checker_sha256: str
    registry_sha256: str
    #: The audited landmark table the scenario names; None for a cell that arms no failpoint.
    landmark_table_sha256: str | None
    #: For a new-evidence cell, the instrument batches whose results allowed it, and the
    #: environment they ran in, which the batch's own manifest must match.
    instrument_batches: tuple[str, ...] = ()
    instrument_fingerprint: dict[str, JsonValue] | None = None

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"


@dataclass
class _Check:
    reasons: list[str]

    def fail(self, reason: str) -> None:
        self.reasons.append(reason)


@dataclass(frozen=True)
class InstrumentEvidence:
    """What the instrument cells' results say: why new evidence may not run yet (empty when it
    may), the batches that showed it, and the one environment they all ran in."""

    missing: list[str]
    batches: list[str]
    fingerprint: dict[str, JsonValue] | None


InstrumentCheck = Callable[[Preregistration, str, Sequence[Path]], InstrumentEvidence]


def builder_problems(
    root: Path, prereg: Preregistration, commits: Sequence[tuple[str, str]]
) -> list[str]:
    """The TrialScenario builder at each named commit is the one the pre-registration pins."""
    pin = prereg.builder
    if pin is None:
        return ["the pre-registration pins no TrialScenario builder"]
    found: list[str] = []
    for name, commit in commits:
        digest = gitproof.blob_sha256(root, commit, pin.path)
        if digest != pin.sha256:
            found.append(
                f"{pin.path} at the {name} commit hashes to {digest}, not the pre-registered "
                f"{pin.sha256}"
            )
    return found


def authorize_batch(
    source: Path | None,
    trials: int,
    root: Path,
    *,
    checker: CheckerView | None = None,
    scenarios: ScenarioView | None = None,
    instrument: Sequence[Path] = (),
    instrument_check: InstrumentCheck | None = None,
) -> Authorization:
    """The authorization for a batch of `trials` trials of the scenario file `source`, or
    NotAuthorized with every reason the batch may not run."""
    check = _Check([])
    checker = checker or T10Checker()
    scenarios = scenarios or T07Scenarios()
    if source is None:
        raise NotAuthorized(["a pre-registered batch runs from a scenario file"])

    # 1. git and the pre-registration
    try:
        run_commit = gitproof.head(root)
        changed = gitproof.dirty(root)
    except gitproof.GitError as exc:
        raise NotAuthorized([f"not a git checkout: {exc}"]) from exc
    if changed:
        check.fail(f"the checkout is not clean ({len(changed)} changed paths)")
    prereg_commit = gitproof.last_commit(root, PREREG)
    committed = None if prereg_commit is None else gitproof.blob(root, prereg_commit, PREREG)
    if prereg_commit is None or committed is None:
        raise NotAuthorized([*check.reasons, f"{PREREG} is not committed"])
    try:
        prereg = parse(committed.decode("utf-8"))
    except PreregistrationError as exc:
        raise NotAuthorized([*check.reasons, f"{PREREG}: {exc}"]) from exc
    if not gitproof.is_ancestor(root, prereg_commit, run_commit):
        check.fail(f"pre-registration commit {prereg_commit} is not an ancestor of {run_commit}")

    # 2. the frozen checker, and the builder of its input
    frozen = _frozen_checker(root, run_commit, prereg, prereg_commit, checker, check)
    commits = (("pre-registration", prereg_commit), ("run", run_commit))
    for reason in builder_problems(root, prereg, commits):
        check.fail(reason)

    # 3. the cell and its scenario
    try:
        rel = PurePosixPath(source.resolve().relative_to(root.resolve()).as_posix())
    except ValueError:
        raise NotAuthorized([*check.reasons, f"{source} is not inside {root}"]) from None
    try:
        cell = prereg.by_scenario(rel.as_posix())
    except KeyError:
        raise NotAuthorized([*check.reasons, f"{rel} is not a pre-registered scenario"]) from None
    for reason in prereg.refusals(cell):
        check.fail(reason)
    landmarks = _scenario(root / rel, cell, trials, scenarios, checker, frozen, check)

    # 4. instrument before new evidence
    evidence = InstrumentEvidence([], [], None)
    if cell.phase == "new-evidence":
        if instrument_check is None:
            check.fail(
                f"cell {cell.id} is new evidence; the instrument cells' results were not given"
            )
        else:
            evidence = instrument_check(prereg, prereg_commit, instrument)
            for reason in evidence.missing:
                check.fail(reason)
            if not evidence.missing and evidence.fingerprint is None:
                check.fail("the instrument cells' environment is unknown")

    if check.reasons or frozen is None or landmarks is None:
        raise NotAuthorized(check.reasons or ["the frozen checker could not be read"])
    return Authorization(
        cell=cell.id,
        phase=cell.phase,
        evidence=cell.evidence,
        scenario=rel.as_posix(),
        scenario_sha256=cast(str, cell.scenario_sha256),
        master_seed=cast(str, cell.master_seed),
        trials=trials,
        run_commit=run_commit,
        preregistration_commit=prereg_commit,
        preregistration_sha256=hashlib.sha256(committed).hexdigest(),
        preregistration_state=prereg.state,
        freeze_commit=frozen.commit,
        frozen_md_sha256=frozen.sha256,
        checker_version=frozen.checker.version,
        checker_sha256=frozen.checker.checker_sha256,
        registry_sha256=frozen.checker.registry_sha256,
        landmark_table_sha256=landmarks.sha256,
        instrument_batches=tuple(evidence.batches),
        instrument_fingerprint=evidence.fingerprint,
    )


def _frozen_checker(
    root: Path,
    run_commit: str,
    prereg: Preregistration,
    prereg_commit: str,
    checker: CheckerView,
    check: _Check,
) -> _Freeze | None:
    try:
        state = checker.freeze_state()
    except Unavailable as exc:
        check.fail(f"no frozen checker: {exc}")
        return None
    if state != "FROZEN":
        check.fail(f"the checker is {state}, not FROZEN")
    text = gitproof.blob(root, run_commit, FROZEN_PATH)
    freeze_commit = gitproof.last_commit(root, FROZEN_PATH)
    if text is None or freeze_commit is None:
        check.fail(f"{FROZEN_PATH} is not committed")
        return None
    digest = hashlib.sha256(text).hexdigest()
    pin = prereg.checker
    if pin is None:
        check.fail("the pre-registration names no frozen checker")
        return None
    if digest != pin.frozen_md_sha256:
        check.fail(
            f"{FROZEN_PATH} hashes to {digest}, not the pre-registered {pin.frozen_md_sha256}"
        )
    if not gitproof.is_ancestor(root, freeze_commit, prereg_commit):
        check.fail(f"freeze commit {freeze_commit} is not an ancestor of the pre-registration")
    try:
        registry = checker.registry_sha256(root)
        frozen = checker.verify_checker(text.decode("utf-8"), registry)
    except Unavailable as exc:
        check.fail(f"no frozen checker: {exc}")
        return None
    except Exception as exc:  # FreezeMismatch, or anything verification raises  # noqa: BLE001
        check.fail(f"the checker does not match {FROZEN_PATH}: {exc}")
        return None
    for name, got, want in (
        ("checker_sha256", frozen.checker_sha256, pin.checker_sha256),
        ("registry_sha256", frozen.registry_sha256, pin.registry_sha256),
        ("checker version", frozen.version, pin.version),
        ("effect models", frozen.effect_models, pin.effect_models),
        ("landmark tables", frozen.landmark_tables, pin.landmark_tables),
    ):
        if got != want:
            check.fail(f"frozen {name} is {got}, the pre-registration names {want}")
    return _Freeze(freeze_commit, text.decode("utf-8"), digest, frozen)


def _scenario(
    path: Path,
    cell: Cell,
    trials: int,
    scenarios: ScenarioView,
    checker: CheckerView,
    frozen: _Freeze | None,
    check: _Check,
) -> _Landmarks | None:
    if trials != cell.n:
        check.fail(f"cell {cell.id} is pre-registered at N = {cell.n}, not {trials}")
    try:
        facts = scenarios.load(path)
    except Unavailable as exc:
        check.fail(f"cannot load the scenario: {exc}")
        return None
    except Exception as exc:  # ScenarioError, a YAML error  # noqa: BLE001
        check.fail(f"{path}: {exc}")
        return None
    if facts.sha256 != cell.scenario_sha256:
        check.fail(
            f"{path} hashes to {facts.sha256}, not the pre-registered {cell.scenario_sha256}"
        )
    if facts.cell != cell.id:
        check.fail(f"{path} names cell {facts.cell}, the pre-registration lists it as {cell.id}")
    if facts.master_seed != cell.master_seed:
        check.fail(f"{path} has master seed {facts.master_seed}, not {cell.master_seed}")
    if facts.workers != cell.workers:
        check.fail(
            f"{path} declares {counted(facts.workers, 'worker')}; cell {cell.id} is "
            f"pre-registered with {cell.workers}"
        )
    if frozen is None:
        return None
    try:
        table = checker.landmark_sha256(frozen.text, frozen.checker.registry_sha256, path)
    except Unavailable as exc:
        check.fail(f"cannot verify the landmark table: {exc}")
        return None
    except Exception as exc:  # FreezeMismatch: unaudited, unfrozen, or changed  # noqa: BLE001
        check.fail(f"{path}: the landmark table does not verify against the freeze: {exc}")
        return None
    if table is None and cell.fault is not None:
        # Every arm is checked against a landmark; without a table each trial would be INVALID.
        check.fail(
            f"cell {cell.id} injects {cell.fault}, so its scenario must name a landmark table"
        )
        return None
    if table is not None and table not in frozen.checker.landmark_tables.values():
        check.fail(f"{path}: landmark table {table} is not one the freeze pins")
        return None
    return _Landmarks(table)


def confirm_environment(
    prereg: Preregistration,
    authorization: Authorization,
    manifest: Manifest,
    rules: Sequence[CompatibilityRule] = (),
) -> list[str]:
    """Why the batch's manifest is not the pre-registered environment; empty when it is. For a
    new-evidence batch, the manifest must also match the instrument batches' environment in every
    material field a recorded compatibility rule does not cover."""
    problems = [
        f"manifest {key} is {got!r}, the pre-registration requires {want!r}"
        for key, (want, got) in mismatched(manifest, prereg.environment).items()
    ]
    if manifest.git_sha != authorization.run_commit:
        problems.append(
            f"manifest names commit {manifest.git_sha}, authorized {authorization.run_commit}"
        )
    if manifest.git_dirty:
        problems.append("the manifest records a dirty checkout")
    if authorization.phase == "new-evidence":
        if authorization.instrument_fingerprint is None:
            problems.append("a new-evidence batch names no instrument environment")
        else:
            differing = unexplained_between(
                authorization.instrument_fingerprint, fingerprint(manifest), rules
            )
            for key, (then, now) in differing.items():
                problems.append(f"{key} is {now!r}; the instrument cells ran with {then!r}")
    return problems


def rules_at(root: Path, commit: str) -> list[CompatibilityRule]:
    """The compatibility rules recorded in the repository at `commit`; none if there is no file."""
    return parse_rules(gitproof.blob(root, commit, RULES_PATH))


def confirm(authorization: Authorization, manifest: Manifest, root: Path) -> list[str]:
    """`confirm_environment` against the pre-registration as committed and the compatibility rules
    as recorded at the authorized commit: what the runner calls after doctor."""
    text = gitproof.blob(root, authorization.preregistration_commit, PREREG)
    if text is None:
        return [f"{PREREG} is not in commit {authorization.preregistration_commit}"]
    rules = rules_at(root, authorization.run_commit)
    return confirm_environment(parse(text.decode("utf-8")), authorization, manifest, rules)
