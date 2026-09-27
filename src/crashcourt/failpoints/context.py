"""The client operation a worker is executing, visible to the saver wrapper.

A context variable carries it: LangGraph copies the context into its background executor and
asyncio copies it into tasks and `to_thread`, so saver calls made on behalf of an operation
see its op_id even when they run on another thread.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_OP_ID: ContextVar[str | None] = ContextVar("crashcourt_op_id", default=None)


def current_op() -> str | None:
    return _OP_ID.get()


@contextmanager
def operation(op_id: str) -> Generator[None]:
    """Attribute every saver call made inside this block to `op_id`."""
    token = _OP_ID.set(op_id)
    try:
        yield
    finally:
        _OP_ID.reset(token)
