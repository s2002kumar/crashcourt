"""The mitigation contract's records against a cell's pre-registered contract predictions.

A measure matches in a trial when the record shows the predicted value; the judgement counts, per
measure, the readable trials that match, and the cell is `as-predicted` only when every measure
matches in every readable trial and no trial was unreadable. A deterministic cell with some trials
matching and some not is `unstable`. Nothing here changes a record or a prediction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from crashcourt.experiment import gitproof
from crashcourt.experiment.gate import AUTHORIZATION_FILE, Authorization
from crashcourt.experiment.preregistration import (
    PREREGISTRATION_PATH,
    Cell,
    ContractPrediction,
    PreregistrationError,
    parse,
)

Status = str  # "as-predicted", "differs", "unstable", "missing"


def predicted_for(batch_dir: Path, repo: Path) -> Cell | None:
    """The batch's cell with its contract prediction, read from the pre-registration as committed
    in the batch's authorized pre-registration commit; None for an unauthorized batch or a cell
    that predicts no contract. Raises for an authorization that names a commit the repository
    lacks."""
    path = batch_dir / AUTHORIZATION_FILE
    if not path.exists():
        return None
    auth = Authorization.model_validate_json(path.read_text("utf-8"))
    raw = gitproof.blob(repo, auth.preregistration_commit, PREREGISTRATION_PATH.as_posix())
    if raw is None:
        raise PreregistrationError(
            f"{PREREGISTRATION_PATH} is not committed at {auth.preregistration_commit}"
        )
    cell = parse(raw.decode("utf-8")).cell(auth.cell)
    return cell if cell.contract is not None else None


def _measures(record: Mapping[str, Any], p: ContractPrediction) -> dict[str, tuple[Any, Any]]:
    """Each measure's (predicted, found) for one readable record."""
    checkpoints = cast(dict[str, Any], record["checkpoints"])
    stale = cast(dict[str, Any] | None, record.get("stale"))
    effects = cast(list[dict[str, Any]], record["effects"])
    fence = cast(dict[str, Any] | None, record.get("fence"))
    found: dict[str, tuple[Any, Any]] = {
        "fork": (p.fork, checkpoints.get("fork")),
        "acknowledged_is_head": (p.acknowledged_is_head, checkpoints.get("acknowledged_is_head")),
        "head_attempts": (list(p.head_attempts), checkpoints.get("head_attempts")),
        "logical": (p.logical, record.get("logical")),
    }
    if p.stale is None:
        found["stale"] = (None, None if stale is None else "a stale run")
    elif stale is None:
        found["stale"] = ("a stale run", None)
    else:
        attempted = sum(cast(dict[str, int], stale["attempted"]).values())
        found["stale.resumed"] = (p.stale.resumed, stale.get("resumed"))
        found["stale.attempted"] = (
            f">= {p.stale.attempted_min}",
            attempted if attempted >= p.stale.attempted_min else f"{attempted} (too few)",
        )
        found["stale.none_durable"] = (
            p.stale.none_durable,
            not any(cast(dict[str, int], stale["durable"]).values()),
        )
        found["stale.every_attempt_fenced"] = (
            p.stale.every_attempt_fenced,
            bool(stale.get("every_attempt_fenced")),
        )
    if len(effects) != 1:
        found["effects"] = ("one logical op", f"{len(effects)} logical ops")
    else:
        [e] = effects
        found["requested"] = (p.requested, e["requested"])
        found["applied"] = (p.applied, e["applied"])
        found["duplicates"] = (p.duplicates, e["duplicates"])
        found["attempt_consistency"] = (p.attempt_consistency, e["attempt_consistency"])
    if p.fence_consistent is None:
        found["fence"] = (None, None if fence is None else "a fence record")
    elif fence is None:
        found["fence"] = ("a fence record", None)
    else:
        found["fence_consistent"] = (p.fence_consistent, fence.get("consistent"))
    return found


def _matches(predicted: Any, found: Any) -> bool:
    if isinstance(predicted, str) and predicted.startswith(">= "):
        return isinstance(found, int)
    return predicted == found


def judge(
    records: Sequence[Mapping[str, Any]], prediction: ContractPrediction, *, deterministic: bool
) -> dict[str, Any]:
    """The judgement of a batch's records against the cell's contract prediction."""
    readable = [r for r in records if not r["problems"]]
    unreadable = [r for r in records if r["problems"]]
    measures: dict[str, dict[str, Any]] = {}
    trials_matched = 0
    for r in readable:
        trial = r.get("trial")
        all_match = True
        for name, (predicted, found) in _measures(r, prediction).items():
            entry = measures.setdefault(
                name, {"predicted": predicted, "matched": 0, "of": 0, "mismatches": {}}
            )
            entry["of"] += 1
            if _matches(predicted, found):
                entry["matched"] += 1
            else:
                all_match = False
                entry["mismatches"][str(trial)] = found
        trials_matched += all_match
    n = len(readable)
    status: Status
    if n == 0:
        status = "missing"
    elif trials_matched == n and not unreadable:
        status = "as-predicted"
    elif deterministic and 0 < trials_matched < n:
        status = "unstable"
    else:
        status = "differs"
    return {
        "status": status,
        "trials": len(records),
        "readable": n,
        "unreadable": len(unreadable),
        "trials_as_predicted": trials_matched,
        "measures": measures,
    }
