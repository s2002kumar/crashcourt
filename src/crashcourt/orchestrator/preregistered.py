"""The pre-registration gate as `crashcourt run` applies it.

A batch is pre-registered when its scenario is one of the cells in research/preregistration.md,
as committed or as in the working tree (draft or final): the same repository-relative file, the
same cell id, or the same content hash. Matching on all three means a pre-registered cell cannot
get past the gate by running a copy of its file from elsewhere, and reading both copies means it
cannot get past by an edit or by leaving the file uncommitted.

A pre-registered batch runs only if:

- it is canonical (`--canonical`), from the scenario file the pre-registration names;
- T-13's gate authorizes it before doctor runs (`crashcourt.experiment.aggregate.authorize`): a
  final pre-registration, a frozen checker, the pre-registered scenario, seed, landmark table,
  and N, and, for new evidence, the instrument batches given (`--instrument`) came out as
  predicted under the compatibility rules recorded at HEAD;
- after doctor, the manifest is the pre-registered environment at the authorized commit, and for
  new evidence the instrument batches' environment (`crashcourt.experiment.gate.confirm`).

Every refusal names its reasons. The authorization goes into the batch directory as
authorization.json, where verification and aggregation look for it. Workers load the harness from
the files on disk, so before each trial and after the last the checkout must still be the
authorized commit with no change; otherwise the batch stops and its authorization is revoked
(renamed to authorization.revoked.json, with the reasons), so it can never be counted.

A batch whose scenario is not pre-registered (smoke runs, fault-free workload runs) runs as before
and carries no authorization: it can never be counted as pre-registered evidence.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from crashcourt.spec.scenario import Scenario, scenario_sha256

if TYPE_CHECKING:
    from crashcourt.experiment.gate import Authorization
    from crashcourt.manifest import Manifest


class GateRefused(Exception):
    """A pre-registered batch may not run; the message says why."""


@dataclass(frozen=True)
class PreregisteredCell:
    id: str
    phase: str
    #: The scenario file the pre-registration names, repository-relative.
    scenario: str


def relative(path: Path, root: Path) -> str | None:
    """`path` as a repository-relative POSIX path, or None outside the checkout."""
    try:
        return PurePosixPath(path.resolve().relative_to(root.resolve()).as_posix()).as_posix()
    except ValueError:
        return None


def preregistered_cell(
    scenario: Scenario, source: Path | None, root: Path
) -> PreregisteredCell | None:
    """The pre-registered cell this batch would run, if any.

    A cell counts when either the pre-registration as last committed or the file in the working
    tree lists it: an edit cannot take a cell out of the gate, and neither can leaving the file
    uncommitted. A copy that cannot be read refuses the batch, since whether it is pre-registered
    could not be decided.
    """
    from crashcourt.experiment import gitproof
    from crashcourt.experiment.preregistration import (
        PREREGISTRATION_PATH,
        Preregistration,
        PreregistrationError,
        parse,
    )

    texts: dict[str, bytes] = {}
    try:
        commit = gitproof.last_commit(root, PREREGISTRATION_PATH)
        committed = None if commit is None else gitproof.blob(root, commit, PREREGISTRATION_PATH)
    except gitproof.GitError as exc:
        raise GateRefused(f"cannot read the pre-registration from git: {exc}") from exc
    if committed is not None:
        texts["as committed"] = committed
    working = root / PREREGISTRATION_PATH
    if working.is_file():
        texts["in the working tree"] = working.read_bytes()

    preregistrations: list[Preregistration] = []
    for where, text in texts.items():
        try:
            preregistrations.append(parse(text.decode("utf-8")))
        except (PreregistrationError, UnicodeDecodeError) as exc:
            raise GateRefused(
                f"{PREREGISTRATION_PATH} {where} cannot be read ({exc}), so whether this batch "
                "is pre-registered cannot be decided"
            ) from exc
    file = relative(source, root) if source is not None else None
    digest = scenario_sha256(scenario)
    for prereg in preregistrations:
        for cell in prereg.cells:
            if cell.scenario == file or cell.id == scenario.cell or cell.scenario_sha256 == digest:
                return PreregisteredCell(cell.id, cell.phase, cell.scenario)
    return None


def authorize(
    cell: PreregisteredCell,
    source: Path | None,
    trials: int,
    root: Path,
    *,
    canonical: bool,
    instrument: Sequence[Path] = (),
) -> Authorization:
    """T-13's authorization for this batch, or GateRefused with every reason it may not run.
    `instrument` names the instrument batches a new-evidence cell rests on."""
    from crashcourt.experiment import aggregate
    from crashcourt.experiment.gate import NotAuthorized

    if source is None or relative(source, root) != cell.scenario:
        raise GateRefused(
            f"pre-registered cell {cell.id} runs only from its scenario file {cell.scenario}"
        )
    if not canonical:
        raise GateRefused(f"pre-registered cell {cell.id} runs only as a canonical batch")
    try:
        return aggregate.authorize(source, trials, root, instrument)
    except NotAuthorized as exc:
        raise GateRefused(
            f"pre-registered cell {cell.id} is not authorized: " + "; ".join(exc.reasons)
        ) from exc


def confirm(authorization: Authorization, manifest: Manifest, root: Path) -> None:
    """GateRefused unless doctor's manifest is the pre-registered environment at the authorized
    commit."""
    from crashcourt.experiment.gate import confirm as confirm_environment

    problems = confirm_environment(authorization, manifest, root)
    if problems:
        raise GateRefused(
            f"cell {authorization.cell}: the environment is not the pre-registered one: "
            + "; ".join(problems)
        )


def record(directory: Path, authorization: Authorization) -> None:
    """Put the authorization in the batch, where verification and aggregation look for it."""
    from crashcourt.experiment.gate import AUTHORIZATION_FILE

    (directory / AUTHORIZATION_FILE).write_bytes(authorization.to_json().encode("utf-8"))


def still_authorized(authorization: Authorization, root: Path) -> list[str]:
    """Why the checkout is no longer the one the batch was authorized at; empty when it is.

    Workers start from the files on disk, so a commit or an edit during the batch would run code
    the manifest does not name.
    """
    from crashcourt.experiment import gitproof

    try:
        head = gitproof.head(root)
        changed = gitproof.dirty(root)
    except gitproof.GitError as exc:
        return [f"cannot read the checkout's state from git: {exc}"]
    problems: list[str] = []
    if head != authorization.run_commit:
        problems.append(
            f"HEAD moved to {head} during the batch; it was authorized at "
            f"{authorization.run_commit}"
        )
    if changed:
        problems.append(f"the checkout changed during the batch ({len(changed)} changed paths)")
    return problems


def revoke(directory: Path, reasons: Sequence[str]) -> None:
    """Take the batch's authorization away, with the reasons: verification and aggregation then
    refuse the batch. The rename comes first and is atomic, so the authorization is never left
    in place."""
    from crashcourt.experiment.gate import AUTHORIZATION_FILE, REVOKED_FILE

    revoked = directory / REVOKED_FILE
    os.replace(directory / AUTHORIZATION_FILE, revoked)
    record = json.loads(revoked.read_text(encoding="utf-8"))
    record["revoked"] = list(reasons)
    revoked.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
