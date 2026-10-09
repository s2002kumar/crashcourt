"""Content hashes of validated models: the scenario hash every batch record carries.

A scenario is loaded from YAML into a frozen, validated Pydantic model, and its hash is taken over
that model's canonical JSON form, never over the YAML bytes. So two files that differ only in
formatting, comments, key order, or in stating a default the model would fill in hash the same,
and any change to what the model holds (the workload, the schedule, a fault, a setting) changes
the hash. T-07's scenario loader produces the value; `batch.json` records it as `scenario_sha256`.

Canonical form: `model_dump(mode="json")`, serialized with sorted keys, no whitespace, and
non-ASCII characters kept as they are. Lists keep their order, since order is meaning in a
schedule.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def canonical_json(model: BaseModel) -> str:
    """The model's content as one JSON string that depends only on its values."""
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def content_sha256(model: BaseModel) -> str:
    """Lowercase sha256 hex of `canonical_json(model)`."""
    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()
