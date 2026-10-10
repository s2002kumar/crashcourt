"""`FailpointSaver`: wraps any `BaseCheckpointSaver`, sync and async, with named failpoints.

Each of `put`, `put_writes`, `get_tuple`, and `list` (and their async forms, under the same
names) runs as: `saver.<op>.call`, the `<op>.before` failpoint, the inner call, the
`<op>.after` failpoint, then `saver.<op>.return`; or `saver.<op>.raise` if the inner call or a
failpoint raised (no `after` failpoint once the inner call has raised). A failpoint that fires
writes `fp.hit`, then `fp.action` just before it acts: for a pause, after the release; for an
exit or a signal, before the process ends. A failpoint that cannot be honored (two arms due on
one call, a pause with no barrier, a signal this host lacks) writes `fp.hit` with status fail
and raises FailpointError.

The wrapper never changes what the inner saver does or returns. Everything else is forwarded:
`serde`, `config_specs`, `get_next_version`, delta-channel history, `delete_thread`, and exactly
the extended capabilities the inner type overrides, so capability detection sees the same saver.
"""

from __future__ import annotations

import asyncio
import functools
import os
import signal
import sys
import threading
from collections.abc import AsyncIterator, Collection, Iterator, Mapping, Sequence
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
)
from pydantic import JsonValue

from crashcourt.failpoints.barrier import Barrier
from crashcourt.failpoints.context import current_op
from crashcourt.failpoints.registry import FailpointError, FailpointRegistry, Firing
from crashcourt.history.writer import HistoryWriter
from crashcourt.spec.events import Event, EventKind

Fields = dict[str, Any]


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _config_fields(config: RunnableConfig | None) -> Fields:
    configurable: Mapping[str, Any] = (config or {}).get("configurable") or {}
    return {
        "thread_id": _text(configurable.get("thread_id")),
        "checkpoint_ns": _text(configurable.get("checkpoint_ns")),
        "checkpoint_id": _text(configurable.get("checkpoint_id")),
    }


def _tuple_fields(result: CheckpointTuple | None, requested: Fields) -> Fields:
    if result is None:
        return {**requested, "data": {"found": False, "requested": requested["checkpoint_id"]}}
    fields = _config_fields(result.config)
    parent = _config_fields(result.parent_config)["checkpoint_id"]
    data: dict[str, JsonValue] = {
        "found": True,
        "requested": requested["checkpoint_id"],
        "pending_writes": len(result.pending_writes or ()),
    }
    return {**fields, "parent_checkpoint_id": parent, "data": data}


class FailpointSaver(BaseCheckpointSaver[Any]):
    def __init__(
        self,
        inner: BaseCheckpointSaver[Any],
        *,
        history: HistoryWriter | None = None,
        registry: FailpointRegistry | None = None,
        barrier: Barrier | None = None,
    ) -> None:
        if registry is not None and history is None:
            raise FailpointError("failpoints need a history writer: every hit is logged")
        super().__init__(serde=inner.serde)
        self.inner = inner
        self.serde = inner.serde
        self.history = history
        self.registry = registry
        self.barrier = barrier

    # -- recording ---------------------------------------------------------------------------

    def _emit(self, kind: EventKind, fields: Fields, **extra: Any) -> Event | None:
        if self.history is None:
            return None
        return self.history.emit(kind, op_id=current_op(), **{**fields, **extra})

    def _raise(self, op: str, fields: Fields, exc: BaseException, stage: str) -> None:
        """`saver.<op>.raise`, with data.stage saying what raised: the before failpoint, the inner
        call, or the after failpoint. After `after`, the inner call's write is already stored."""
        kind: EventKind = f"saver.{op}.raise"  # type: ignore[assignment]
        data: dict[str, JsonValue] = {
            **cast("dict[str, JsonValue]", fields.get("data") or {}),
            "stage": stage,
        }
        error = f"{type(exc).__name__}: {exc}"
        self._emit(kind, {**fields, "data": data}, status="fail", error=error)

    # -- failpoints --------------------------------------------------------------------------

    def _check(self, point: str, fields: Fields) -> tuple[Firing, Event] | None:
        if self.registry is None:
            return None
        hit_fields = {k: v for k, v in fields.items() if k != "data"}
        firing: Firing | None = None
        try:
            firing = self.registry.check(point, current_op(), fields.get("thread_id"))
            if firing is not None and firing.arm.action == "pause" and self.barrier is None:
                raise FailpointError(f"{point} is armed to pause but the saver has no barrier")
            wanted = firing.arm.signal if firing is not None else None
            if wanted is not None and (sys.platform == "win32" or not hasattr(signal, wanted)):
                raise FailpointError(f"{wanted} cannot be sent to a thread on this host")
        except FailpointError as exc:
            if firing is not None:
                self.registry.failed_to_act(firing.arm)
            self._emit("fp.hit", hit_fields, fp=point, status="fail", error=str(exc))
            raise
        if firing is None:
            return None
        data: dict[str, JsonValue] = {"action": firing.arm.action, "occurrence": firing.occurrence}
        hit = self._emit("fp.hit", hit_fields, fp=point, data=data)
        assert hit is not None  # a registry implies a history writer
        return firing, hit

    def _act(self, firing: Firing, hit: Event) -> None:
        arm = firing.arm
        data: dict[str, JsonValue] = {"hit_seq": hit.seq, "action": arm.action}
        if arm.signal is not None:
            data["signal"] = arm.signal
        self._emit("fp.action", {"thread_id": hit.thread_id}, fp=hit.fp, data=data)
        if arm.action == "exit":
            os._exit(1 if arm.exit_code is None else arm.exit_code)
        if arm.action == "signal" and sys.platform != "win32":
            # To this thread, not the process: a signal for the calling thread is delivered before
            # pthread_kill returns. Sent to the process, it can land on another thread and the call
            # here goes on; LangGraph runs saver calls on its background executor's threads.
            signal.pthread_kill(threading.get_ident(), int(getattr(signal, str(arm.signal))))

    def _fire(self, point: str, fields: Fields) -> None:
        found = self._check(point, fields)
        if found is None:
            return
        firing, hit = found
        if firing.arm.action == "pause":
            assert self.barrier is not None
            self.barrier.wait(hit)
        self._act(firing, hit)

    async def _afire(self, point: str, fields: Fields) -> None:
        found = self._check(point, fields)
        if found is None:
            return
        firing, hit = found
        if firing.arm.action == "pause":
            assert self.barrier is not None
            # Park only this coroutine; the rest of the event loop keeps running.
            await asyncio.to_thread(self.barrier.wait, hit)
        self._act(firing, hit)

    # -- base operations ---------------------------------------------------------------------

    def _put_fields(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> Fields:
        parent = _config_fields(config)
        data: dict[str, JsonValue] = {
            "step": metadata.get("step"),
            "source": metadata.get("source"),
            "channels": list[JsonValue](sorted(new_versions)),
        }
        return {
            "thread_id": parent["thread_id"],
            "checkpoint_ns": parent["checkpoint_ns"],
            "checkpoint_id": checkpoint["id"],
            "parent_checkpoint_id": parent["checkpoint_id"],
            "data": data,
        }

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        fields = self._put_fields(config, checkpoint, metadata, new_versions)
        self._emit("saver.put.call", fields)
        stage = "before"
        try:
            self._fire("put.before", fields)
            stage = "inner"
            result = self.inner.put(config, checkpoint, metadata, new_versions)
            stage = "after"
            self._fire("put.after", fields)
        except BaseException as exc:
            self._raise("put", fields, exc, stage)
            raise
        self._emit("saver.put.return", {**fields, "data": {}}, status="ok")
        return result

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        fields = self._put_fields(config, checkpoint, metadata, new_versions)
        self._emit("saver.put.call", fields)
        stage = "before"
        try:
            await self._afire("put.before", fields)
            stage = "inner"
            result = await self.inner.aput(config, checkpoint, metadata, new_versions)
            stage = "after"
            await self._afire("put.after", fields)
        except BaseException as exc:
            self._raise("put", fields, exc, stage)
            raise
        self._emit("saver.put.return", {**fields, "data": {}}, status="ok")
        return result

    def _writes_fields(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str,
    ) -> Fields:
        data: dict[str, JsonValue] = {"channels": [c for c, _ in writes], "task_path": task_path}
        return {**_config_fields(config), "task_id": task_id, "data": data}

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        fields = self._writes_fields(config, writes, task_id, task_path)
        self._emit("saver.put_writes.call", fields)
        stage = "before"
        try:
            self._fire("put_writes.before", fields)
            stage = "inner"
            self.inner.put_writes(config, writes, task_id, task_path)
            stage = "after"
            self._fire("put_writes.after", fields)
        except BaseException as exc:
            self._raise("put_writes", fields, exc, stage)
            raise
        self._emit("saver.put_writes.return", {**fields, "data": {}}, status="ok")

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        fields = self._writes_fields(config, writes, task_id, task_path)
        self._emit("saver.put_writes.call", fields)
        stage = "before"
        try:
            await self._afire("put_writes.before", fields)
            stage = "inner"
            await self.inner.aput_writes(config, writes, task_id, task_path)
            stage = "after"
            await self._afire("put_writes.after", fields)
        except BaseException as exc:
            self._raise("put_writes", fields, exc, stage)
            raise
        self._emit("saver.put_writes.return", {**fields, "data": {}}, status="ok")

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        fields = _config_fields(config)
        self._emit("saver.get_tuple.call", fields)
        stage = "before"
        try:
            self._fire("get_tuple.before", fields)
            stage = "inner"
            result = self.inner.get_tuple(config)
            stage = "after"
            self._fire("get_tuple.after", fields)
        except BaseException as exc:
            self._raise("get_tuple", fields, exc, stage)
            raise
        self._emit("saver.get_tuple.return", _tuple_fields(result, fields), status="ok")
        return result

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        fields = _config_fields(config)
        self._emit("saver.get_tuple.call", fields)
        stage = "before"
        try:
            await self._afire("get_tuple.before", fields)
            stage = "inner"
            result = await self.inner.aget_tuple(config)
            stage = "after"
            await self._afire("get_tuple.after", fields)
        except BaseException as exc:
            self._raise("get_tuple", fields, exc, stage)
            raise
        self._emit("saver.get_tuple.return", _tuple_fields(result, fields), status="ok")
        return result

    def _list_fields(
        self,
        config: RunnableConfig | None,
        before: RunnableConfig | None,
        limit: int | None,
        filter: dict[str, Any] | None,
    ) -> Fields:
        data: dict[str, JsonValue] = {
            "before": _config_fields(before)["checkpoint_id"],
            "limit": limit,
            "filtered": filter is not None,
        }
        return {**_config_fields(config), "data": data}

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        fields = self._list_fields(config, before, limit, filter)
        self._emit("saver.list.call", fields)
        count = 0
        items: Iterator[CheckpointTuple] | None = None
        stage = "before"
        try:
            self._fire("list.before", fields)
            stage = "inner"
            items = self.inner.list(config, filter=filter, before=before, limit=limit)
            for item in items:
                count += 1
                yield item
            stage = "after"
            self._fire("list.after", fields)
        except GeneratorExit:
            # Close the inner iterator too, so a saver holding a cursor releases it now; the
            # call is recorded as ended early even if closing fails.
            try:
                close = getattr(items, "close", None)
                if close is not None:
                    close()
            finally:
                data = {"items": count, "complete": False}
                self._emit("saver.list.return", {**fields, "data": data})
            raise
        except BaseException as exc:
            self._raise("list", fields, exc, stage)
            raise
        data = {"items": count, "complete": True}
        self._emit("saver.list.return", {**fields, "data": data}, status="ok")

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        fields = self._list_fields(config, before, limit, filter)
        self._emit("saver.list.call", fields)
        count = 0
        items: AsyncIterator[CheckpointTuple] | None = None
        stage = "before"
        try:
            await self._afire("list.before", fields)
            stage = "inner"
            items = self.inner.alist(config, filter=filter, before=before, limit=limit)
            async for item in items:
                count += 1
                yield item
            stage = "after"
            await self._afire("list.after", fields)
        except GeneratorExit:
            try:
                aclose = getattr(items, "aclose", None)
                if aclose is not None:
                    await aclose()
            finally:
                data = {"items": count, "complete": False}
                self._emit("saver.list.return", {**fields, "data": data})
            raise
        except BaseException as exc:
            self._raise("list", fields, exc, stage)
            raise
        data = {"items": count, "complete": True}
        self._emit("saver.list.return", {**fields, "data": data}, status="ok")

    # -- forwarded without failpoints -------------------------------------------------------

    def delete_thread(self, thread_id: str) -> None:
        self.inner.delete_thread(thread_id)

    async def adelete_thread(self, thread_id: str) -> None:
        await self.inner.adelete_thread(thread_id)

    @property
    def config_specs(self) -> list[Any]:
        # Upstream annotates config_specs as a bare `list`.
        specs: list[Any] = self.inner.config_specs  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return specs

    def get_next_version(self, current: Any, channel: None) -> Any:
        return self.inner.get_next_version(current, channel)

    def get_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        return self.inner.get_delta_channel_history(config=config, channels=channels)

    async def aget_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        return await self.inner.aget_delta_channel_history(config=config, channels=channels)

    def with_allowlist(self, extra_allowlist: Collection[tuple[str, ...]]) -> FailpointSaver:
        inner = self.inner.with_allowlist(extra_allowlist)
        if inner is self.inner:  # nothing changed; keep this wrapper, as the inner saver does
            return self
        return wrap(
            inner,
            history=self.history,
            registry=self.registry,
            barrier=self.barrier,
        )


#: Optional capabilities (sync and async forms) that are forwarded only if the inner type has them.
EXTENDED_METHODS: tuple[str, ...] = (
    "delete_for_runs",
    "adelete_for_runs",
    "copy_thread",
    "acopy_thread",
    "prune",
    "aprune",
)


def _forward(name: str) -> Any:
    if name.startswith("a"):

        async def forward_async(self: FailpointSaver, *args: Any, **kwargs: Any) -> Any:
            return await getattr(self.inner, name)(*args, **kwargs)

        forward_async.__name__ = name
        return forward_async

    def forward(self: FailpointSaver, *args: Any, **kwargs: Any) -> Any:
        return getattr(self.inner, name)(*args, **kwargs)

    forward.__name__ = name
    return forward


@functools.cache
def wrapper_class(inner_type: type[BaseCheckpointSaver[Any]]) -> type[FailpointSaver]:
    """`FailpointSaver`, plus forwarding for exactly the extended methods `inner_type` defines."""
    overridden = {
        name: _forward(name)
        for name in EXTENDED_METHODS
        if getattr(inner_type, name, None) is not getattr(BaseCheckpointSaver, name, None)
    }
    if not overridden:
        return FailpointSaver
    return type(f"FailpointSaver_{inner_type.__name__}", (FailpointSaver,), overridden)


def wrap(
    inner: BaseCheckpointSaver[Any],
    *,
    history: HistoryWriter | None = None,
    registry: FailpointRegistry | None = None,
    barrier: Barrier | None = None,
) -> FailpointSaver:
    """Wrap `inner`; the result offers exactly the capabilities `inner` offers."""
    cls = wrapper_class(type(inner))
    return cls(inner, history=history, registry=registry, barrier=barrier)
