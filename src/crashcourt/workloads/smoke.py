"""The smoke workload: two nodes in a line, no model, no effects.

It exists to exercise the harness (workers, dispatch, barriers, histories) on a real saver and a
real database with as little graph as possible. Each run appends its two node names to the
thread's trace, so a thread that two runs continued shows four entries.
"""

from __future__ import annotations

import operator
from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue


class SmokeState(TypedDict):
    trace: Annotated[list[str], operator.add]


def first(state: SmokeState) -> dict[str, list[str]]:
    return {"trace": ["first"]}


def second(state: SmokeState) -> dict[str, list[str]]:
    return {"trace": ["second"]}


def build(checkpointer: BaseCheckpointSaver[Any]) -> Any:
    """The compiled graph. langgraph's graph module ships no type information."""
    graph: Any = StateGraph(SmokeState)
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    return graph.compile(checkpointer=checkpointer)


def summarize(output: Mapping[str, Any]) -> JsonValue:
    """What the client is told: the thread's trace after the run."""
    return {"trace": [str(name) for name in output.get("trace", [])]}
