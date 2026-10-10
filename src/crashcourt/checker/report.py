"""checker.json: the per-trial record beside result.json.

Schema v1's result record has no field for the checker version, so each trial's checker.json
carries it: the version, the freeze state, the hashes the classification rests on, the trigger
that made the trial INVALID, the invariants that were not exercised (omitted from result.json,
never PASS), and observations that are not outcomes.
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from crashcourt.checker.status import FreezeState

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class NotExercised(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invariant: str
    missing: str


class InvalidTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger: Annotated[int, Field(ge=1, le=15)]
    name: str
    reason: str


class CheckerReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checker_version: str
    freeze_state: FreezeState
    #: False while PRE-FREEZE: these results may not be used as new evidence.
    authorized_for_new_evidence: bool
    #: The checker hash FROZEN.md records; null before the freeze.
    frozen_sha256: Sha256 | None
    #: The hash of the checker files that produced this record.
    checker_sha256: Sha256
    registry_sha256: Sha256
    effect_model_sha256: Sha256
    landmark_table_sha256: Sha256 | None
    batch: str
    cell: str
    trial: int
    invalid: InvalidTrigger | None = None
    not_exercised: tuple[NotExercised, ...] = ()
    #: Records that are not outcomes: an accepted run with no durable record, observed heads.
    observations: tuple[str, ...] = ()
    #: Anomalies that point at the harness first (a duplicate with no fault, for one).
    harness_investigations: tuple[str, ...] = ()
    fault_notes: tuple[str, ...] = ()


def to_json(model: BaseModel) -> str:
    """Canonical bytes for a record: sorted keys, two-space indent, one trailing newline."""
    return (
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )
