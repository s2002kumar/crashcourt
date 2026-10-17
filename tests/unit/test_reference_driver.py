"""The C9 driver's pure parts: normalization of the two final states, and the dispatch order."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from crashcourt.spec.scenario import load_scenario
from tests.controls.c9 import C9Refused, dispatch_order, final_state, normalize

ROOT = Path(__file__).resolve().parents[2]
THREAD = "b/K1a/1/t1"


def test_normalization_replaces_the_thread_id_and_reduces_messages() -> None:
    assert normalize(f"charge:{THREAD}", THREAD) == "charge:<thread>"
    assert normalize([[f"charge:{THREAD}", 1]], THREAD) == [["charge:<thread>", 1]]
    assert normalize({"b": 1, "a": None}, THREAD) == {"a": None, "b": 1}
    messages = [
        HumanMessage(content="First request.", id="human-1"),
        AIMessage(content=f"Hello [thread {THREAD}]", id=f"ai:{THREAD}:respond:0:1"),
    ]
    assert normalize(messages, THREAD) == [
        ["human", "human-1", "First request."],
        ["ai", "ai:<thread>:respond:0:1", "Hello [thread <thread>]"],
    ]
    with pytest.raises(TypeError):
        normalize(object(), THREAD)


def test_final_state_compares_the_workloads_channels_and_marks_absent_ones() -> None:
    values = {"order": "order-17", "decision": "approve", "executed": [[f"charge:{THREAD}", 1]]}
    assert final_state(values, "w1", THREAD) == {
        "order": "order-17",
        "plan": "<absent>",
        "resume": "<absent>",
        "decision": "approve",
        "executed": [["charge:<thread>", 1]],
    }
    assert final_state({}, "w5", THREAD) == {"messages": "<absent>"}


def test_dispatch_order_follows_after() -> None:
    k2 = load_scenario(ROOT / "scenarios/first-matrix/K2.yaml")
    assert [op.id for op in dispatch_order(k2)] == ["turn-1", "turn-2", "turn-3"]
    cyclic = k2.model_copy(
        update={"ops": tuple(op.model_copy(update={"after": "turn-3"}) for op in k2.ops)}
    )
    with pytest.raises(C9Refused):
        dispatch_order(cyclic)
