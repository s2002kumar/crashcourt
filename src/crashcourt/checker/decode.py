"""Strict decoding of blob and write values. A decoding failure makes the trial INVALID.

Values are decoded with the pinned serializer under strict msgpack: only the serializer's built-in
allowlist of safe types, no pickle fallback (the equivalent of LANGGRAPH_STRICT_MSGPACK=true,
fixed here rather than read from the environment). The lineage invariants read raw columns only,
so a serializer defect cannot fake or hide them; decoded values feed only the effect and resume
invariants.
"""

from __future__ import annotations

import functools
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class DecodeError(Exception):
    """A value the checker needs failed strict decoding."""


@functools.cache
def _serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        pickle_fallback=False, allowed_json_modules=None, allowed_msgpack_modules=None
    )


def decode(type_: str, blob: bytes | None) -> Any:
    """The value a (type, blob) pair stores."""
    if type_ == "empty":
        raise DecodeError("an empty blob holds no value")
    try:
        return _serializer().loads_typed((type_, blob if blob is not None else b""))
    except Exception as exc:
        raise DecodeError(f"{type_} value failed strict decoding: {exc}") from exc


def encode(value: Any) -> tuple[str, bytes]:
    """The (type, blob) pair the pinned serializer writes for `value`; used to build fixtures."""
    return _serializer().dumps_typed(value)
