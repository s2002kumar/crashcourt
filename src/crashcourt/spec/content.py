"""Content hashes of validated models: the scenario hash every batch record carries.

A scenario is loaded from YAML into a frozen, validated Pydantic model, and its hash is taken over
that model's canonical JSON form, never over the YAML bytes. So two files that differ only in
formatting, comments, key order, or in stating a default the model would fill in hash the same,
and any change to what the model holds (the workload, the schedule, a fault, a setting) changes
the hash. T-07's scenario loader produces the value; `batch.json` records it as `scenario_sha256`.

Canonical form: `model_dump(mode="json")`, serialized with sorted keys, no whitespace, and
non-ASCII characters kept as they are. Lists keep their order, since order is meaning in a
schedule. Two things a model must not hold, because the form would stop being canonical: a set
(its dump order changes from one process to the next, so it is refused) and a non-finite float
(JSON has no NaN or infinity, so it is refused). A model that should hash across versions of
itself carries its own version field, which then enters the hash.
"""

from __future__ import annotations

import hashlib
import json
import types
import typing
from typing import Any

from pydantic import BaseModel


class NotCanonical(TypeError):
    """The model cannot be hashed canonically; its definition needs to change."""


def _holds_a_set(annotation: Any, seen: set[Any]) -> bool:
    origin = typing.get_origin(annotation)
    if origin in (set, frozenset) or annotation in (set, frozenset):
        return True
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return False
        seen.add(annotation)
        return any(_holds_a_set(f.annotation, seen) for f in annotation.model_fields.values())
    if origin is not None or isinstance(annotation, types.UnionType):
        return any(_holds_a_set(arg, seen) for arg in typing.get_args(annotation))
    return False


def canonical_json(model: BaseModel) -> str:
    """The model's content as one JSON string that depends only on its values."""
    if _holds_a_set(type(model), set()):
        raise NotCanonical(f"{type(model).__name__} holds a set; its dump order is not stable")
    try:
        return json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as exc:  # json.dumps refusing NaN or infinity
        raise NotCanonical(f"{type(model).__name__} holds a non-finite number: {exc}") from exc


def content_sha256(model: BaseModel) -> str:
    """Lowercase sha256 hex of `canonical_json(model)`."""
    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()
