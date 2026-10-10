"""W5, concurrent continuation: user turns on one thread, each answered by the scripted model.

The graph is one node, respond, which appends the scripted model's answer to the message list.
Each op adds one user message (input `{"messages": [{"role": "user", "content": ..., "id":
...}]}`). The answer's text and id name the thread, the turn, and the attempt, so the saved
messages show which attempt of which turn produced each answer.

Two turns run one after the other on one worker, or are sent to two workers at once and held at
a barrier: then both continue the same checkpoint, which is the forked lineage the checker looks
for (T-10). The workload itself has no effects.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.message import add_messages  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue

from crashcourt.workloads.harness import Harness
from crashcourt.workloads.model import ScriptedModel

MODEL = ScriptedModel(
    {"respond": ["Hello. What would you like to do?", "Noted: your second request is queued."]}
)


class W5State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def build(checkpointer: BaseCheckpointSaver[Any], harness: Harness) -> Any:
    def respond(state: W5State, config: RunnableConfig) -> dict[str, Any]:
        turn = sum(1 for message in state["messages"] if message.type == "human") - 1
        with harness.node("respond", config, turn=turn) as run:
            answer = MODEL.respond(
                thread_id=run.thread_id, node="respond", turn=turn, attempt=run.attempt
            )
            return {"messages": [answer]}

    graph: Any = StateGraph(W5State)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)


def summarize(output: Mapping[str, Any]) -> JsonValue:
    """What the client is told: every message on the thread, as type, id, and text."""
    return {
        "messages": [[str(m.type), str(m.id), str(m.content)] for m in output.get("messages", [])]
    }
