from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from crashcourt.spec.records import OUTCOMES, VERDICTS, BatchRecord, InvariantResult, ResultRecord

DIGEST = "a" * 64
EVIDENCE = ["trials/17/merged.jsonl#/41"]
REVIEW = {"reviewer": "maintainer", "reviewed": "2026-10-08"}
CONFIRMED: dict[str, Any] = {
    "clause": "LG-DUR-SYNC",
    "evidence": EVIDENCE,
    "repro": "repros/F-0001",
    "review": REVIEW,
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
        ("EXPECTED", {"clause": "LG-DUR-ASYNC", "evidence": EVIDENCE}),
        ("EXPECTED", {"clause": "LG-DUR-ASYNC", "evidence": EVIDENCE, "repro": "repros/E-1"}),
        ("GAP", {"evidence": EVIDENCE}),
        ("NO_ANOMALY_UNSPECIFIED", {}),
        ("INVALID", {"reason": "control C1 failed"}),
        ("VIOLATION_CANDIDATE", {"clause": "LG-DUR-SYNC", "evidence": EVIDENCE}),
        ("VIOLATION_CANDIDATE", {"clause": "LG-DUR-SYNC", "evidence": EVIDENCE, "repro": "r/1"}),
        ("VIOLATION", CONFIRMED),
    ],
)
def test_each_outcome_with_what_it_needs_is_accepted(name: str, fields: dict[str, Any]) -> None:
    assert outcome(name, **fields).outcome == name


@pytest.mark.parametrize(
    ("name", "fields", "needs"),
    [
        ("VIOLATION", {**CONFIRMED, "repro": None}, "needs a de-harnessed repro"),
        ("VIOLATION", {**CONFIRMED, "review": None}, "needs a review"),
        ("VIOLATION", {**CONFIRMED, "evidence": []}, "needs evidence"),
        ("VIOLATION", {**CONFIRMED, "clause": None}, "needs a clause"),
        ("VIOLATION_CANDIDATE", {"clause": "LG-PW"}, "needs evidence"),
        ("VIOLATION_CANDIDATE", {**CONFIRMED}, "has no review"),
        ("PASS", {}, "needs a clause"),
        ("PASS", {"clause": "LG-PW", "review": REVIEW}, "has no review"),
        ("PASS", {"clause": "LG-PW", "repro": "r/1"}, "has no repro"),
        ("EXPECTED", {"evidence": EVIDENCE}, "needs a clause"),
        ("EXPECTED", {"clause": "LG-PW"}, "needs evidence"),
        ("EXPECTED", {"clause": "LG-PW", "evidence": EVIDENCE, "review": REVIEW}, "has no review"),
        ("GAP", {}, "needs evidence"),
        ("GAP", {"clause": "LG-PW", "evidence": EVIDENCE}, "has no clause"),
        ("GAP", {"evidence": EVIDENCE, "review": REVIEW}, "has no review"),
        ("NO_ANOMALY_UNSPECIFIED", {"clause": "LG-PW"}, "has no clause"),
        ("NO_ANOMALY_UNSPECIFIED", {"repro": "r/1"}, "has no repro"),
        ("INVALID", {}, "needs a reason"),
        ("INVALID", {"reason": "x", "repro": "r/1"}, "has no repro"),
    ],
)
def test_an_outcome_without_what_it_needs_is_rejected(
    name: str, fields: dict[str, Any], needs: str
) -> None:
    with pytest.raises(ValidationError, match=needs):
        outcome(name, **fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"clause": " "},
        {"clause": "LG PW"},
        {"clause": ""},
        {"evidence": ["  "]},
        {"reason": "   "},
        {"reason": " leading space"},
    ],
)
def test_blank_or_padded_text_is_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        outcome("INVALID", **{"reason": "control C1 failed", **fields})


def test_a_review_date_must_be_a_real_date() -> None:
    review = outcome("VIOLATION", **CONFIRMED).review
    assert review is not None and review.reviewed == date(2026, 10, 8)
    bad = {**CONFIRMED, "review": {"reviewer": "m", "reviewed": "2026-99-99"}}
    with pytest.raises(ValidationError):
        outcome("VIOLATION", **bad)


def result(**changes: Any) -> dict[str, Any]:
    return {
        "v": 1,
        "batch": "b-1",
        "cell": "c-1",
        "trial": 0,
        "provenance": "run",
        "manifest_sha256": DIGEST,
        "results": [{"invariant": "I1", "outcome": "NO_ANOMALY_UNSPECIFIED"}],
        **changes,
    }


def test_records_carry_provenance_and_the_manifest_hash() -> None:
    batch = BatchRecord(v=1, batch="b-1", provenance="run", manifest_sha256=DIGEST)
    fixture = ResultRecord.model_validate(result(batch="DEMO-b-1", provenance="fixture"))
    assert (batch.provenance, fixture.provenance) == ("run", "fixture")
    assert fixture.manifest_sha256 == batch.manifest_sha256


@pytest.mark.parametrize(("batch", "provenance"), [("DEMO-b-1", "run"), ("b-1", "fixture")])
def test_only_fixtures_have_demo_batch_ids(batch: str, provenance: str) -> None:
    record = {"v": 1, "batch": batch, "provenance": provenance, "manifest_sha256": DIGEST}
    with pytest.raises(ValidationError, match="DEMO-"):
        BatchRecord.model_validate(record)
    with pytest.raises(ValidationError, match="DEMO-"):
        ResultRecord.model_validate(result(batch=batch, provenance=provenance))


def test_one_outcome_per_invariant() -> None:
    twice = [
        {"invariant": "I1", "outcome": "PASS", "clause": "LG-DUR-SYNC"},
        {
            "invariant": "I1",
            "outcome": "VIOLATION_CANDIDATE",
            "clause": "LG-DUR-SYNC",
            "evidence": EVIDENCE,
        },
    ]
    with pytest.raises(ValidationError, match="repeated: I1"):
        ResultRecord.model_validate(result(results=twice))


@pytest.mark.parametrize(
    "changes",
    [
        {"v": True},
        {"v": 2},
        {"provenance": "demo"},
        {"manifest_sha256": "ABC"},
        {"results": []},
        {"trial": -1},
        {"batch": "b 1"},
        {"extra": 1},
    ],
)
def test_malformed_results_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ResultRecord.model_validate(result(**changes))


CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "records-v1.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_models_decide_each_shared_case_as_recorded(case: dict[str, Any]) -> None:
    # The evidence explorer checks the same cases against the JSON Schemas (site tests).
    model = BatchRecord if case["kind"] == "batch" else ResultRecord
    if case["valid"]:
        model.model_validate(case["record"])
    else:
        with pytest.raises(ValidationError):
            model.model_validate(case["record"])
