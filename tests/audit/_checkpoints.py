"""Checkpoints built the way the runtime builds them, for direct-saver audit tests (T-03).

The audit tests call only the savers' public methods. They never edit rows or patch saver code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.base.id import uuid6

#: The format the langgraph 1.2.12 runtime writes (pregel/_checkpoint.py, LATEST_VERSION = 4).
#: langgraph.checkpoint.base.empty_checkpoint() still produces the legacy version 2.
RUNTIME_CHECKPOINT_VERSION = 4


def new_thread() -> str:
    return f"audit-{uuid4()}"


def config(thread_id: str, checkpoint_id: str | None = None) -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def checkpoint(step: int, values: dict[str, Any], versions: ChannelVersions) -> Checkpoint:
    """A checkpoint whose id comes from uuid6(clock_seq=step), as in the Pregel loop."""
    return Checkpoint(
        v=RUNTIME_CHECKPOINT_VERSION,
        id=str(uuid6(clock_seq=step)),
        ts=datetime.now(UTC).isoformat(),
        channel_values=dict(values),
        channel_versions=dict(versions),
        versions_seen={},
        updated_channels=None,
    )


def metadata(step: int) -> CheckpointMetadata:
    return {"source": "loop", "step": step, "parents": {}}
