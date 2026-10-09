"""The scenario hash depends on what a validated model holds, not on how its YAML was written."""

from __future__ import annotations

import re
from typing import Literal

import pytest
import yaml
from pydantic import BaseModel, ConfigDict

from crashcourt.spec.content import canonical_json, content_sha256


class Fault(BaseModel):
    """A stand-in for T-07's scenario model: enough shape to show what the hash covers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["kill", "hold", "drop"]
    at: str
    worker: str = "worker-1"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workload: str
    durability: Literal["sync", "async", "exit"] = "sync"
    workers: int = 1
    faults: tuple[Fault, ...] = ()
    settings: dict[str, int] = {}


def load(text: str) -> Scenario:
    return Scenario.model_validate(yaml.safe_load(text))


BASE = """
workload: w1-approve
durability: sync
workers: 2
faults:
  - kind: kill
    at: put.after
    worker: worker-1
settings:
  trials: 20
  timeout_s: 30
"""

EQUIVALENT = [
    # Keys in another order, comments, different quoting and spacing.
    """
# the flagship cell
settings: {timeout_s: 30, trials: 20}
workers: 2
faults:
- {worker: "worker-1", at: 'put.after', kind: kill}
durability: "sync"
workload: 'w1-approve'
""",
    # A default stated explicitly inside the fault, and the top-level default left implicit.
    """
workload: w1-approve
workers: 2
faults:
  - kind: kill
    at: put.after
settings:
  trials: 20
  timeout_s: 30
""",
]

CHANGED = {
    "another workload": BASE.replace("w1-approve", "w1-reject"),
    "another durability": BASE.replace("durability: sync", "durability: async"),
    "one worker": BASE.replace("workers: 2", "workers: 1"),
    "a fault at another point": BASE.replace("put.after", "put.before"),
    "a fault on the other worker": BASE.replace("worker: worker-1", "worker: worker-2"),
    "no faults": BASE.replace(
        "faults:\n  - kind: kill\n    at: put.after\n    worker: worker-1\n", "faults: []\n"
    ),
    "a setting": BASE.replace("trials: 20", "trials: 21"),
}


def test_the_hash_is_lowercase_sha256_hex() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", content_sha256(load(BASE)))


@pytest.mark.parametrize("text", EQUIVALENT, ids=["reordered and commented", "defaults implicit"])
def test_equivalent_yaml_hashes_the_same(text: str) -> None:
    assert load(text) == load(BASE)
    assert content_sha256(load(text)) == content_sha256(load(BASE))


@pytest.mark.parametrize("text", CHANGED.values(), ids=list(CHANGED))
def test_a_change_of_meaning_changes_the_hash(text: str) -> None:
    assert load(text) != load(BASE)
    assert content_sha256(load(text)) != content_sha256(load(BASE))


def test_fault_order_is_meaning() -> None:
    two = BASE.replace(
        "faults:\n  - kind: kill\n    at: put.after\n    worker: worker-1\n",
        "faults:\n  - {kind: kill, at: put.after}\n  - {kind: hold, at: get_tuple.after}\n",
    )
    swapped = two.replace(
        "  - {kind: kill, at: put.after}\n  - {kind: hold, at: get_tuple.after}\n",
        "  - {kind: hold, at: get_tuple.after}\n  - {kind: kill, at: put.after}\n",
    )
    assert content_sha256(load(two)) != content_sha256(load(swapped))


def test_the_canonical_form_is_sorted_compact_json_of_the_values() -> None:
    text = canonical_json(load(BASE))
    assert text.startswith('{"durability":"sync","faults":[{"at":"put.after","kind":"kill"')
    assert " " not in text and "\n" not in text
