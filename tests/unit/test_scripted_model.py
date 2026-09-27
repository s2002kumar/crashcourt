from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.sink.ledger import Ledger, TrialId
from crashcourt.workloads.model import ScriptedModel, attempt_key

MODEL = ScriptedModel({"plan": ["Plan: charge order 17.", "Plan: refund order 17."]})


def test_same_key_same_response() -> None:
    a = MODEL.respond(thread_id="t3", node="plan", turn=0, attempt=1)
    b = MODEL.respond(thread_id="t3", node="plan", turn=0, attempt=1)
    assert (a.content, a.id) == (b.content, b.id)


def test_every_response_names_its_attempt() -> None:
    first = MODEL.respond(thread_id="t3", node="plan", turn=0, attempt=1)
    second = MODEL.respond(thread_id="t3", node="plan", turn=0, attempt=2)
    assert first.content != second.content and first.id != second.id
    assert "attempt 2" in str(second.content) and second.response_metadata["attempt"] == 2


def test_turns_follow_the_script_and_wrap() -> None:
    texts = [
        str(MODEL.respond(thread_id="t", node="plan", turn=t, attempt=1).content) for t in range(3)
    ]
    assert texts[0].startswith("Plan: charge") and texts[1].startswith("Plan: refund")
    assert texts[2].startswith("Plan: charge")


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"node": "missing", "turn": 0, "attempt": 1}, KeyError),
        ({"node": "plan", "turn": 0, "attempt": 0}, ValueError),
        ({"node": "plan", "turn": -1, "attempt": 1}, ValueError),
    ],
)
def test_bad_keys_are_rejected(kwargs: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        MODEL.respond(thread_id="t", **kwargs)  # type: ignore[arg-type]


def test_attempts_come_from_the_sink_counter_and_survive_a_new_worker(tmp_path: Path) -> None:
    trial = TrialId("DEMO-b", "DEMO-c", 0, "s")
    ledger = Ledger(trial.path(tmp_path), trial)
    first = MODEL.call(ledger.next_attempt, thread_id="t3", node="plan", turn=0)
    # A replacement worker asks the same sink: it gets the next attempt, not the first again.
    second = MODEL.call(ledger.next_attempt, thread_id="t3", node="plan", turn=0)
    ledger.close()
    assert (first.response_metadata["attempt"], second.response_metadata["attempt"]) == (1, 2)
    assert attempt_key("t3", "plan", 0) == "model/t3/plan/0"
