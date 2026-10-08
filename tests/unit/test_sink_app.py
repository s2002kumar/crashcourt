from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from crashcourt.sink.app import create_app
from crashcourt.sink.source import source_sha256

TRIAL = {"batch": "DEMO-b", "cell": "DEMO-c", "trial": 1, "seed": "s"}


def effect(**changes: Any) -> dict[str, Any]:
    return {
        "effect_key": "charge:order-17",
        "logical_op": "approve:t3:i1",
        "op_id": "op-7",
        "attempt": 1,
        "worker": "worker-1",
        "pid": 4242,
        "thread_id": "t3",
        "node": "execute",
        "checkpoint_id_seen": "C4",
        "payload_hash": hashlib.sha256(b"charge").hexdigest(),
        **changes,
    }


def post(body: dict[str, Any], trial: dict[str, Any] = TRIAL) -> dict[str, Any]:
    return {"trial": trial, "effect": body}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_health_reports_no_trial_and_no_hooks(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["trial"] is None and body["test_hooks"] is False
    assert body["source_sha256"] == source_sha256()


def test_effects_need_an_open_trial(client: TestClient) -> None:
    assert client.post("/effects", json=post(effect())).status_code == 409
    assert client.post("/attempts/next", json={"trial": TRIAL, "key": "k"}).status_code == 409


def test_effects_are_appended_and_never_deduplicated(client: TestClient) -> None:
    assert client.post("/trials", json=TRIAL).json() == {"trial": TRIAL, "rows": 0}
    seqs = [client.post("/effects", json=post(effect())).json()["sink_seq"] for _ in range(3)]
    assert seqs == [1, 2, 3]
    rows = client.get("/ledger").json()["rows"]
    assert [r["sink_seq"] for r in rows] == [1, 2, 3]
    assert client.get("/ledger/verify").json() == {"ok": True, "rows": 3, "first_bad": None}


def test_attempt_counters(client: TestClient) -> None:
    client.post("/trials", json=TRIAL)
    values = [
        client.post("/attempts/next", json={"trial": TRIAL, "key": "t3/execute/0"}).json()
        for _ in range(3)
    ]
    assert values == [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}]


def test_malformed_effect_is_rejected(client: TestClient) -> None:
    client.post("/trials", json=TRIAL)
    assert client.post("/effects", json=post(effect(attempt=0))).status_code == 422
    assert client.post("/effects", json=post(effect(extra="x"))).status_code == 422
    assert client.get("/ledger").json()["rows"] == []


def test_a_restarted_sink_resumes_the_open_trial(tmp_path: Path) -> None:
    first = TestClient(create_app(tmp_path))
    first.post("/trials", json=TRIAL)
    first.post("/effects", json=post(effect()))
    second = TestClient(create_app(tmp_path))  # a new process over the same data directory
    assert second.get("/health").json()["trial"] == TRIAL
    assert second.post("/effects", json=post(effect())).json()["sink_seq"] == 2
    assert second.get("/ledger/verify").json()["ok"] is True


def test_switching_trials_keeps_each_ledger(client: TestClient) -> None:
    client.post("/trials", json=TRIAL)
    client.post("/effects", json=post(effect()))
    client.post("/trials", json={**TRIAL, "trial": 2})
    assert client.get("/ledger").json()["rows"] == []
    assert client.post("/trials", json=TRIAL).json()["rows"] == 1


def test_a_request_for_another_trial_is_refused(client: TestClient) -> None:
    client.post("/trials", json={**TRIAL, "trial": 2})
    late = client.post("/effects", json=post(effect()))  # the previous trial's worker, late
    assert late.status_code == 409 and "open trial" in late.json()["detail"]
    counter = client.post("/attempts/next", json={"trial": TRIAL, "key": "k"})
    assert counter.status_code == 409
    assert client.get("/ledger").json()["rows"] == []
    assert client.post("/effects", json=post(effect(), {**TRIAL, "trial": 2})).status_code == 200


@pytest.mark.parametrize("field", ["logical_op", "pid", "thread_id", "node"])
def test_effect_fields_the_ledger_needs_are_required(client: TestClient, field: str) -> None:
    client.post("/trials", json=TRIAL)
    body = {k: v for k, v in effect().items() if k != field}
    assert client.post("/effects", json=post(body)).status_code == 422
    assert client.post("/effects", json=post(effect(**{field: None}))).status_code == 422


def test_the_open_trial_is_recorded_without_temporary_files(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    client.post("/trials", json=TRIAL)
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["current.json"]


def test_record_effect_refuses_another_workers_effect(tmp_path: Path) -> None:
    from crashcourt.history.writer import HistoryWriter, RunIdentity
    from crashcourt.sink.client import SinkClient, record_effect
    from crashcourt.sink.ledger import EffectRequest

    run = RunIdentity("DEMO-b", "DEMO-c", 1, "s")
    with HistoryWriter(tmp_path / "w.jsonl", run=run, proc="worker-1", pid=4242) as history:
        for changes in ({"worker": "worker-2"}, {"pid": 4243}):
            with pytest.raises(ValueError, match="recorded by worker-1"):
                record_effect(
                    history, SinkClient("http://127.0.0.1:9"), EffectRequest(**effect(**changes))
                )
    assert (tmp_path / "w.jsonl").read_bytes() == b""  # refused before anything was recorded
