"""A cell's predicted observations, matched against one trial's records.

Each observation is read from what the oracle or the checker wrote, never from what the target
reports about itself: the effect ledger's export (ledger.json: the sink's rows and its own verify
answer), checker.json's records that are not outcomes, and whether result.json holds an anomaly
outcome for an invariant at all. Nothing here classifies anything; an observation matches or it
does not, and the mismatch says what the trial showed instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from crashcourt.experiment.preregistration import (
    AnomalySeen,
    CheckerRecord,
    LedgerRows,
    Observation,
    counted,
)
from crashcourt.experiment.verify import TrialFiles

#: Outcomes that say the checker found an anomaly; they differ only in the clause that covers it.
ANOMALY: frozenset[str] = frozenset({"EXPECTED", "GAP", "VIOLATION_CANDIDATE", "VIOLATION"})
#: The effect ledger's export, which T-07's post-trial step writes into each trial directory.
LEDGER_FILE = "ledger.json"


def _ledger(directory: Path) -> tuple[list[dict[str, Any]], str | None]:
    """The ledger rows of a trial, or why they cannot be read; an export the sink did not verify
    is not read at all."""
    path = directory / LEDGER_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], f"no {LEDGER_FILE}"
    except (OSError, ValueError) as exc:
        return [], f"{LEDGER_FILE} is unreadable: {exc}"
    export = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    if export.get("verify_ok") is not True:
        return [], f"{LEDGER_FILE} is not an export the sink verified"
    rows = export.get("rows")
    if not isinstance(rows, list):
        return [], f"{LEDGER_FILE} holds no list of rows"
    checked: list[dict[str, Any]] = []
    for r in cast(list[Any], rows):
        row = cast(dict[str, Any], r) if isinstance(r, dict) else {}
        if not isinstance(row.get("effect_key"), str) or type(row.get("attempt")) is not int:
            return [], f"{LEDGER_FILE} rows lack an effect key or an attempt"
        checked.append(row)
    return checked, None


def outcome(t: TrialFiles, invariant: str) -> str | None:
    """The invariant's outcome in the trial's result.json, if it has one."""
    for r in (t.result or {}).get("results", []):
        if r.get("invariant") == invariant:
            return str(r.get("outcome"))
    return None


def mismatch(o: Observation, t: TrialFiles) -> str | None:
    """None when the trial shows the observation; otherwise what it showed instead."""
    match o:
        case LedgerRows():
            rows, problem = _ledger(t.directory)
            if problem is not None:
                return problem
            mine = [r for r in rows if str(r["effect_key"]).split(":", 1)[0] == o.effect]
            attempts = sorted(int(r["attempt"]) for r in mine)
            if len(mine) != o.rows or (o.attempts is not None and tuple(attempts) != o.attempts):
                return f"{counted(len(mine), o.effect + ' row')}, attempts {attempts}"
            return None
        case CheckerRecord():
            records = cast(list[object], t.checker.get("observations") or [])
            found = [r for r in records if isinstance(r, str) and r.startswith(o.starts_with)]
            if len(found) != o.count:
                return f"{counted(len(found), 'checker record')} beginning {o.starts_with!r}"
            return None
        case AnomalySeen():
            value = outcome(t, o.invariant)
            if (value in ANOMALY) != o.present:
                return f"{o.invariant} is {value or 'absent from result.json'}"
            return None
