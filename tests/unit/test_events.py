from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from crashcourt.spec.events import EVENT_KINDS, SCHEMA_VERSION, Event

EXAMPLE = {
    "v": 1,
    "batch": "b-2026-10-07-01",
    "cell": "pg-async-sync-W1-F06",
    "trial": 17,
    "seed": "9f2c",
    "proc": "worker-2",
    "pid": 4242,
    "seq": 131,
    "mono_ns": 88231455120931,
    "wall_ns": 1791461100000000000,
    "kind": "saver.put.return",
    "op_id": "op-0007",
    "logical_op": "approve:t3:i1",
    "attempt": 2,
    "thread_id": "t3",
    "checkpoint_ns": "",
    "checkpoint_id": "1f0a",
    "parent_checkpoint_id": "1f09",
    "task_id": None,
    "fp": "put.after",
    "fault_id": None,
    "status": "ok",
    "error": None,
    "data": {},
}


def event(**changes: Any) -> dict[str, Any]:
    return {**EXAMPLE, **changes}


def test_the_example_validates_and_round_trips() -> None:
    parsed = Event.model_validate(EXAMPLE)
    assert parsed.v == SCHEMA_VERSION == 1
    again = Event.model_validate_json(parsed.to_line())
    assert again == parsed


def test_line_is_one_compact_json_object_with_every_field_in_envelope_order() -> None:
    line = Event.model_validate(EXAMPLE).to_line()
    assert line.endswith("\n") and line.count("\n") == 1
    keys = list(json.loads(line))
    example_order = [k for k in EXAMPLE if k != "data"]
    # effect_key is not in the example line; it sits after attempt.
    expected = [*example_order[:14], "effect_key", *example_order[14:], "data"]
    assert keys == expected


def test_kinds_are_exactly_the_v1_set() -> None:
    savers = {
        f"saver.{m}.{p}"
        for m in ("put", "put_writes", "get_tuple", "list")
        for p in ("call", "return", "raise")
    }
    assert set(EVENT_KINDS) == {
        "client.invoke",
        "client.ok",
        "client.fail",
        "client.info",
        "dispatch.send",
        "dispatch.redeliver",
        "worker.start",
        "worker.exit",
        "node.start",
        "node.end",
        *savers,
        "fp.hit",
        "fp.release",
        "fp.action",
        "effect.request",
        "effect.response",
        "sink.append",
        "fault.inject",
        "fault.heal",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("v", 2),
        ("v", True),
        ("v", 1.0),
        ("kind", "saver.delete_thread.call"),
        ("kind", "client.retry"),
        ("seq", -1),
        ("trial", -1),
        ("status", "maybe"),
        ("proc", ""),
        ("batch", ""),
        ("attempt", 0),
        ("data", []),
    ],
)
def test_invalid_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(event(**{field: value}))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(event(worker="worker-2"))


def test_booleans_are_not_integers() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(event(seq=True))


@pytest.mark.parametrize(
    ("kind", "fields", "missing"),
    [
        ("dispatch.send", {"op_id": "op-1", "data": {}}, "to"),
        ("dispatch.redeliver", {"op_id": None, "data": {"to": "worker-1"}}, "op_id"),
        ("worker.exit", {"data": {}}, "proc"),
        ("fp.hit", {"fp": None}, "fp"),
        ("fp.release", {"data": {"proc": "worker-1"}}, "hit_seq"),
        ("fp.action", {"data": {}}, "hit_seq"),
        ("effect.request", {"effect_key": None}, "effect_key"),
        ("sink.append", {"effect_key": "k", "data": {}}, "worker"),
        ("sink.append", {"effect_key": "k", "data": {"worker": "worker-1"}}, "sink_seq"),
        ("effect.response", {"effect_key": "k", "status": "ok", "data": {}}, "sink_seq"),
        ("client.invoke", {"op_id": None}, "op_id"),
        ("fault.inject", {"fault_id": None}, "fault_id"),
        ("client.ok", {"op_id": "op-1", "data": {"worker": "worker-1"}}, "worker_seq"),
        ("client.fail", {"op_id": "op-1", "data": {"worker_seq": 3}}, "data.worker"),
    ],
)
def test_kind_specific_requirements(kind: str, fields: dict[str, Any], missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        Event.model_validate(event(kind=kind, **fields))


def test_kind_specific_requirements_accept_complete_events() -> None:
    base = event(op_id="op-1", attempt=1, effect_key="charge:1", fault_id="f-1", fp="put.before")
    for kind, data in [
        ("dispatch.send", {"to": "worker-1"}),
        ("worker.exit", {"proc": "worker-1", "returncode": -9}),
        ("fp.release", {"proc": "worker-1", "hit_seq": 4}),
        ("fp.action", {"hit_seq": 4, "action": "pause"}),
        ("sink.append", {"worker": "worker-1", "sink_seq": 3}),
        ("fault.inject", {"toxic": "timeout"}),
    ]:
        Event.model_validate({**base, "kind": kind, "data": data})


def test_a_failed_effect_response_names_no_ledger_row() -> None:
    fields = {"kind": "effect.response", "effect_key": "k", "status": "fail", "data": {}}
    assert Event.model_validate(event(**fields)).status == "fail"
    ok = {**fields, "status": "ok", "data": {"sink_seq": 4}}
    assert Event.model_validate(event(**ok)).data == {"sink_seq": 4}
