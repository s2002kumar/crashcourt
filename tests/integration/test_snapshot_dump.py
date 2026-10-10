"""The snapshot dump against real saver rows, read back by the checker.

A fault-free W1-shaped graph (plan, an approval that interrupts, execute, record) runs on the
pinned Postgres saver under durability="sync": one start and one resume with "approve". The dump
must then give the checker a single-chain lineage, strictly decodable values, the decision the
resume carried in every RESUME write, and the blob the executed channel names. A harness check of
the reader on the lab database, not an experiment cell.

Usage: docker compose up -d --wait && uv run pytest -m integration
"""

from __future__ import annotations

import os
import uuid
from typing import Any, TypedDict

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from crashcourt.checker.effect_models import W1
from crashcourt.checker.lineage import ChannelReader, build_lineages, channel_versions
from crashcourt.checker.state import decode_states
from crashcourt.probes import DEFAULT_PG_DSN
from crashcourt.snapshot.dump import pretrial_rows, take, take_pair

pytestmark = pytest.mark.integration

DSN = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)


class State(TypedDict, total=False):
    plan: str
    decision: str
    receipt: Any
    executed: list[Any]


def _graph(thread: str) -> StateGraph[State]:
    def plan(state: State) -> State:
        return {"plan": "p1"}

    def approval(state: State) -> State:
        return {"decision": str(interrupt("approve?"))}

    def execute(state: State) -> State:
        return {
            "receipt": [f"charge:{thread}", 1] if state.get("decision") == "approve" else "none"
        }

    def record(state: State) -> State:
        receipt = state.get("receipt")
        return {"executed": [receipt] if isinstance(receipt, list) else []}

    builder = StateGraph(State)
    builder.add_node("plan", plan)
    builder.add_node("approval", approval)
    builder.add_node("execute", execute)
    builder.add_node("record", record)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approval")
    builder.add_edge("approval", "execute")
    builder.add_edge("execute", "record")
    builder.add_edge("record", END)
    return builder


def test_the_dump_gives_the_checker_what_it_needs() -> None:
    thread = f"snapshot-check/{uuid.uuid4()}"
    assert pretrial_rows(DSN, [thread]) == {thread: 0}
    config: Any = {"configurable": {"thread_id": thread}}
    try:
        with PostgresSaver.from_conn_string(DSN) as saver:
            saver.setup()
            graph = _graph(thread).compile(checkpointer=saver)
            graph.invoke({}, config, durability="sync")
            graph.invoke(Command(resume="approve"), config, durability="sync")
        pair = take_pair(DSN, [thread], {thread: 0}, after_orchestrator_seq=0, gap_s=0.2)
        snapshot = take(DSN, [thread], after_orchestrator_seq=0)
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread,))  # pyright: ignore[reportArgumentType]

    assert pair.first.same_rows(pair.second)
    assert snapshot.meta.settings == {
        "fsync": "on",
        "synchronous_commit": "on",
        "track_commit_timestamp": "on",
    }
    assert "password" not in snapshot.meta.dsn
    assert snapshot.meta.postmaster_start_ns > 0
    rows = [*snapshot.checkpoints, *snapshot.blobs, *snapshot.writes]
    assert rows and all(r.commit_ns is not None for r in rows)

    lineage = build_lineages(snapshot)[(thread, "")]
    assert len(lineage.roots) == 1 and not lineage.forks and not lineage.dangling
    assert lineage.head == max(lineage.rows)
    assert len(lineage.head_path()) == len(lineage.rows)

    reader = ChannelReader(snapshot)
    head = lineage.rows[str(lineage.head)]
    assert "executed" in channel_versions(head) and not reader.inline(head, "executed")
    assert reader.blob(head, "executed") is not None
    assert reader.inline(head, "decision")

    state = decode_states((thread,), build_lineages(snapshot), reader, W1)[thread]
    assert state.head_executed == ((f"charge:{thread}", 1),)
    assert state.head_decision == "approve"
    # The null task's write of the Command's value and the interrupted node's own RESUME list.
    assert [d for d, _ in state.resume_decisions] == ["approve", "approve"]
    tasks = {w.task_id for w in snapshot.writes if w.channel == "__resume__"}
    assert "00000000-0000-0000-0000-000000000000" in tasks and len(tasks) == 2
    assert any(w.channel == "__interrupt__" for w in snapshot.writes)
