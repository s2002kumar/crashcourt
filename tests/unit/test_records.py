from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from crashcourt.spec.records import OUTCOMES, VERDICTS, BatchRecord, InvariantResult, ResultRecord

DIGEST = "a" * 64
CONFIRMED: dict[str, Any] = {
    "clause": "LG-DUR-SYNC",
    "evidence": ["trials/17/merged.jsonl#/41"],
    "repro": "repros/F-0001",
    "review": {"reviewer": "maintainer", "reviewed": "2026-10-08"},
}


def outcome(name: str, **fields: Any) -> InvariantResult:
    return InvariantResult.model_validate({"invariant": "I1", "outcome": name, **fields})


def test_the_five_verdicts_are_outcomes_and_the_two_others_are_not_verdicts() -> None:
    assert VERDICTS == ("PASS", "VIOLATION", "EXPECTED", "GAP", "INVALID")
    assert set(OUTCOMES) - set(VERDICTS) == {"VIOLATION_CANDIDATE", "NO_ANOMALY_UNSPECIFIED"}


@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("PASS", {"clause": "LG-DUR-SYNC"}),
        ("EXPECTED", {"clause": "LG-DUR-ASYNC"}),
        ("GAP", {}),
        ("NO_ANOMALY_UNSPECIFIED", {}),
        ("INVALID", {"reason": "control C1 failed"}),
        ("VIOLATION_CANDIDATE", {"clause": "LG-DUR-SYNC", "evidence": ["trials/17/x.json"]}),
        ("VIOLATION", CONFIRMED),
    ],
)
def test_each_outcome_with_what_it_needs_is_accepted(name: str, fields: dict[str, Any]) -> None:
    assert outcome(name, **fields).outcome == name


@pytest.mark.parametrize(
    ("name", "fields", "needs"),
    [
        ("VIOLATION", {**CONFIRMED, "repro": None}, "de-harnessed repro"),
        ("VIOLATION", {**CONFIRMED, "review": None}, "a review"),
        ("VIOLATION", {**CONFIRMED, "evidence": []}, "evidence"),
        ("VIOLATION", {**CONFIRMED, "clause": None}, "a clause"),
        ("VIOLATION_CANDIDATE", {"clause": "LG-PW"}, "evidence"),
        ("VIOLATION_CANDIDATE", {**CONFIRMED}, "no review yet"),
        ("PASS", {}, "a clause"),
        ("EXPECTED", {}, "a clause"),
        ("GAP", {"clause": "LG-PW"}, "no clause"),
        ("NO_ANOMALY_UNSPECIFIED", {"clause": "LG-PW"}, "no clause"),
        ("INVALID", {}, "a reason"),
    ],
)
def test_an_outcome_without_what_it_needs_is_rejected(
    name: str, fields: dict[str, Any], needs: str
) -> None:
    with pytest.raises(ValidationError, match=needs):
        outcome(name, **fields)


def test_records_carry_provenance_and_the_manifest_hash() -> None:
    batch = BatchRecord(v=1, batch="b-1", provenance="run", manifest_sha256=DIGEST)
    result = ResultRecord(
        v=1,
        batch="b-1",
        cell="c-1",
        trial=0,
        provenance="fixture",
        manifest_sha256=DIGEST,
        results=(outcome("GAP"),),
    )
    assert (batch.provenance, result.provenance) == ("run", "fixture")
    assert result.manifest_sha256 == batch.manifest_sha256


@pytest.mark.parametrize(
    "changes",
    [
        {"v": True},
        {"v": 2},
        {"provenance": "demo"},
        {"manifest_sha256": "ABC"},
        {"results": []},
        {"trial": -1},
        {"extra": 1},
    ],
)
def test_malformed_results_are_rejected(changes: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "v": 1,
        "batch": "b-1",
        "cell": "c-1",
        "trial": 0,
        "provenance": "run",
        "manifest_sha256": DIGEST,
        "results": [{"invariant": "I1", "outcome": "GAP"}],
    }
    with pytest.raises(ValidationError):
        ResultRecord.model_validate({**base, **changes})
