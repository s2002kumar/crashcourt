"""Does user code run before the input checkpoint's put completes under durability="sync"? (T-03)

Delays every put by 0.3 s and records, in order, when each put starts and ends and when user code
runs, for a StateGraph and for a functional-API entrypoint. In-process, against InMemorySaver:

    cd research/evidence/t-03
    uv run python first_tick_order.py > first_tick_order.txt
"""

import threading
import time
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import entrypoint
from langgraph.graph import START, StateGraph

events: list[str] = []
lock = threading.Lock()


def log(event: str) -> None:
    with lock:
        events.append(event)


class SlowSaver(InMemorySaver):
    def put(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        source, step = metadata.get("source"), metadata.get("step")
        log(f"put start {source} {step}")
        time.sleep(0.3)
        result = super().put(config, checkpoint, metadata, new_versions)
        log(f"put end   {source} {step}")
        return result


class State(TypedDict):
    x: int


def node(state: State) -> State:
    log("USER CODE (StateGraph node)")
    return {"x": state["x"] + 1}


def run_state_graph() -> list[str]:
    events.clear()
    builder = StateGraph(State)
    builder.add_node("node", node)
    builder.add_edge(START, "node")
    graph = builder.compile(checkpointer=SlowSaver())
    graph.invoke({"x": 0}, {"configurable": {"thread_id": "sg"}}, durability="sync")
    return list(events)


def run_entrypoint() -> list[str]:
    events.clear()

    @entrypoint(checkpointer=SlowSaver())
    def workflow(x: int) -> int:
        log("USER CODE (entrypoint body)")
        return x + 1

    workflow.invoke(0, {"configurable": {"thread_id": "ep"}}, durability="sync")
    return list(events)


if __name__ == "__main__":
    import langgraph.version

    print("langgraph", langgraph.version.__version__)
    for name, fn in (("StateGraph", run_state_graph), ("entrypoint", run_entrypoint)):
        print(f"--- {name}, durability=sync")
        for e in fn():
            print("   ", e)
