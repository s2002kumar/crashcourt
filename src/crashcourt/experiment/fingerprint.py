"""A batch's environment fingerprint, and the rule for when two batches may be pooled.

Trials from different batches are pooled into one cell's counts only when nothing material
differs: the runner, the platform, the interpreter, the lockfile and every pinned package, the
database and proxy builds and settings, the clause registry, the specification, and the harness
commit. Every manifest field is material except the three that describe one run rather than its
environment: when it was generated, whether the tree was dirty (the gate requires it clean), and
the sink's measured clock offset (recorded, never an input to a verdict).

Fail closed: a difference in any material field keeps the batches apart, unless a compatibility
rule recorded in the repository names that exact field and both values with a reason. Rules are
data a reviewer can read; nothing is pooled by default.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from crashcourt.manifest import Manifest

#: Manifest fields that describe one run, not the environment it ran in.
NOT_MATERIAL: frozenset[str] = frozenset({"generated_at", "git_dirty", "sink_clock_offset_ns"})


def _flatten(prefix: str, value: Any, into: dict[str, JsonValue]) -> None:
    if isinstance(value, Mapping):
        for key in sorted(value):  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            _flatten(f"{prefix}.{key}" if prefix else str(key), value[key], into)  # pyright: ignore[reportUnknownArgumentType]
    else:
        into[prefix] = value


def fingerprint(manifest: Manifest) -> dict[str, JsonValue]:
    """The material fields of a manifest, flattened to dotted paths (`packages.langgraph`)."""
    found: dict[str, JsonValue] = {}
    for name, value in manifest.to_dict().items():
        if name not in NOT_MATERIAL:
            _flatten(name, value, found)
    return dict(sorted(found.items()))


def fingerprint_sha256(manifest: Manifest) -> str:
    """sha256 of the fingerprint's canonical JSON: equal exactly when the fingerprints are."""
    text = json.dumps(fingerprint(manifest), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CompatibilityRule(BaseModel):
    """A recorded decision that one material field may differ between pooled batches."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    #: The values that may be pooled with each other, all of them listed.
    values: tuple[JsonValue, ...] = Field(min_length=2)
    reason: str = Field(min_length=1)


def differences(a: Manifest, b: Manifest) -> dict[str, tuple[JsonValue, JsonValue]]:
    """Every material field whose value differs, with both values (missing is None)."""
    fa, fb = fingerprint(a), fingerprint(b)
    return {
        key: (fa.get(key), fb.get(key))
        for key in sorted(fa.keys() | fb.keys())
        if fa.get(key) != fb.get(key)
    }


def unexplained_between(
    fa: Mapping[str, JsonValue],
    fb: Mapping[str, JsonValue],
    rules: Iterable[CompatibilityRule] = (),
) -> dict[str, tuple[JsonValue, JsonValue]]:
    """Fingerprint differences no recorded rule covers; empty means they may be pooled."""
    allowed = list(rules)

    def covered(key: str, pair: tuple[JsonValue, JsonValue]) -> bool:
        return any(r.field == key and pair[0] in r.values and pair[1] in r.values for r in allowed)

    found = {
        key: (fa.get(key), fb.get(key))
        for key in sorted(fa.keys() | fb.keys())
        if fa.get(key) != fb.get(key)
    }
    return {k: v for k, v in found.items() if not covered(k, v)}


def unexplained(
    a: Manifest, b: Manifest, rules: Iterable[CompatibilityRule] = ()
) -> dict[str, tuple[JsonValue, JsonValue]]:
    """The differences no recorded rule covers; empty means the batches may be pooled."""
    return unexplained_between(fingerprint(a), fingerprint(b), rules)


#: Where the recorded compatibility rules live: a JSON list, absent when there are none.
RULES_PATH = PurePosixPath("research/compatibility.json")


def parse_rules(text: str | bytes | None) -> list[CompatibilityRule]:
    """The rules a research/compatibility.json text records; none when there is no file."""
    if text is None:
        return []
    raw: Any = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError(f"{RULES_PATH} holds a JSON list of rules")
    return [CompatibilityRule.model_validate(r) for r in cast(list[Any], raw)]


def mismatched(manifest: Manifest, required: Mapping[str, JsonValue]) -> dict[str, tuple[Any, Any]]:
    """Required dotted-path values the manifest does not hold, as (required, actual)."""
    flat: dict[str, JsonValue] = {}
    _flatten("", manifest.to_dict(), flat)
    return {
        key: (want, flat.get(key, "<absent>"))
        for key, want in sorted(required.items())
        if flat.get(key, "<absent>") != want
    }
