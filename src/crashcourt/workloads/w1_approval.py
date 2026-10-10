"""W1, approval before an effect: plan, approval, execute, record.

- plan asks the scripted model for a plan.
- approval pauses at `interrupt()`; the resume value (approve or reject) becomes `resume`.
- execute posts one effect, `charge:<thread>`, only on approve, after the interrupt. The request
  records the decision it acted on (`data.decision`), the value execute received.
- record writes `decision` and `executed` (the effect key and attempt of every charge) into
  state.

So approve fires exactly one effect and reject none, and the saved decision says which.

The misuse variant (`w1-misuse`) posts `precheck:<thread>` from approval before `interrupt()`.
LangGraph re-runs an interrupted node from its beginning on resume, so the precheck fires once
before the pause and again on resume, whatever the decision. That duplicate is documented
behavior (the clause LG-INT-RERUN), and the variant exists to show it every time.

A run is two client ops on one thread: the start (input `{"order": ...}`) ends interrupted, and
the resume (`resume: approve` or `reject`) runs to the end.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.types import interrupt
from pydantic import JsonValue

from crashcourt.workloads.harness import Harness
from crashcourt.workloads.model import ScriptedModel

MODEL = ScriptedModel({"plan": ["Charge the order once a person approves it."]})
APPROVE = "approve"


class W1State(TypedDict, total=False):
    order: str
    plan: str
    resume: str
    charged: list[list[JsonValue]]
    decision: str
    executed: list[list[JsonValue]]


def _build(checkpointer: BaseCheckpointSaver[Any], harness: Harness, *, misuse: bool) -> Any:
    def plan(state: W1State, config: RunnableConfig) -> dict[str, Any]:
        with harness.node("plan", config) as run:
            message = MODEL.respond(
                thread_id=run.thread_id, node="plan", turn=0, attempt=run.attempt
            )
            return {"plan": str(message.content)}

    def approval(state: W1State, config: RunnableConfig) -> dict[str, Any]:
        with harness.node("approval", config) as run:
            if misuse:  # the effect sits before interrupt(), so it fires on every attempt
                run.post_effect(
                    f"precheck:{run.thread_id}",
                    {"effect": "precheck", "order": state.get("order", "")},
                )
            value = interrupt({"plan": state.get("plan", "")})
            return {"resume": value}

    def execute(state: W1State, config: RunnableConfig) -> dict[str, Any]:
        with harness.node("execute", config) as run:
            decision = state.get("resume")
            if decision != APPROVE:
                return {"charged": []}
            key = f"charge:{run.thread_id}"
            payload: dict[str, JsonValue] = {
                "effect": "charge",
                "order": state.get("order", ""),
                "decision": decision,
            }
            run.post_effect(key, payload, data={"decision": decision})
            return {"charged": [[key, run.attempt]]}

    def record(state: W1State, config: RunnableConfig) -> dict[str, Any]:
        with harness.node("record", config):
            return {"decision": state.get("resume"), "executed": list(state.get("charged", []))}

    graph: Any = StateGraph(W1State)
    graph.add_node("plan", plan)
    graph.add_node("approval", approval)
    graph.add_node("execute", execute)
    graph.add_node("record", record)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "approval")
    graph.add_edge("approval", "execute")
    graph.add_edge("execute", "record")
    graph.add_edge("record", END)
    return graph.compile(checkpointer=checkpointer)


def build(checkpointer: BaseCheckpointSaver[Any], harness: Harness) -> Any:
    return _build(checkpointer, harness, misuse=False)


def build_misuse(checkpointer: BaseCheckpointSaver[Any], harness: Harness) -> Any:
    return _build(checkpointer, harness, misuse=True)


def summarize(output: Mapping[str, Any]) -> JsonValue:
    """What the client is told: the state after the op, and whether it ended at the interrupt."""
    summary: dict[str, JsonValue] = {
        key: output[key]
        for key in ("order", "plan", "resume", "decision", "executed")
        if key in output
    }
    interrupts: list[Any] = list(output.get("__interrupt__") or [])
    summary["interrupted"] = bool(interrupts)
    if interrupts:
        summary["interrupt"] = [getattr(item, "value", None) for item in interrupts]
    return summary
