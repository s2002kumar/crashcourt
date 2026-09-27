from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from crashcourt.sink.app import create_app

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


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_health_reports_no_trial_and_no_hooks(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["trial"] is None and body["test_hooks"] is False


def test_effects_need_an_open_trial(client: TestClient) -> None:
    assert client.post("/effects", json=effect()).status_code == 409
    assert client.post("/attempts/next", json={"key": "k"}).status_code == 409


def test_effects_are_appended_and_never_deduplicated(client: TestClient) -> None:
    assert client.post("/trials", json=TRIAL).json() == {"trial": TRIAL, "rows": 0}
    seqs = [client.post("/effects", json=effect()).json()["sink_seq"] for _ in range(3)]
    assert seqs == [1, 2, 3]
    rows = client.get("/ledger").json()["rows"]
    assert [r["sink_seq"] for r in rows] == [1, 2, 3]
    assert client.get("/ledger/verify").json() == {"ok": True, "rows": 3, "first_bad": None}


def test_attempt_counters(client: TestClient) -> None:
    client.post("/trials", json=TRIAL)
    values = [client.post("/attempts/next", json={"key": "t3/execute/0"}).json() for _ in range(3)]
    assert values == [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}]


def test_malformed_effect_is_rejected(client: TestClient) -> None:
    client.post("/trials", json=TRIAL)
    assert client.post("/effects", json=effect(attempt=0)).status_code == 422
    assert client.post("/effects", json=effect(extra="x")).status_code == 422
    assert client.get("/ledger").json()["rows"] == []


def test_a_restarted_sink_resumes_the_open_trial(tmp_path: Path) -> None:
    first = TestClient(create_app(tmp_path))
    first.post("/trials", json=TRIAL)
    first.post("/effects", json=effect())
    second = TestClient(create_app(tmp_path))  # a new process over the same data directory
    assert second.get("/health").json()["trial"] == TRIAL
    assert second.post("/effects", json=effect()).json()["sink_seq"] == 2
    assert second.get("/ledger/verify").json()["ok"] is True


def test_switching_trials_keeps_each_ledger(client: TestClient) -> None:
    client.post("/trials", json=TRIAL)
    client.post("/effects", json=effect())
    client.post("/trials", json={**TRIAL, "trial": 2})
    assert client.get("/ledger").json()["rows"] == []
    assert client.post("/trials", json=TRIAL).json()["rows"] == 1
