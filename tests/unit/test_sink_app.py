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
    assert isinstance(body["mono_ns"], int)


def test_effects_need_an_open_trial(client: TestClient) -> None:
    assert client.post("/effects", json=post(effect())).status_code == 409
    assert client.post("/attempts/next", json={"trial": TRIAL, "key": "k"}).status_code == 409


def test_effects_are_appended_and_never_deduplicated(client: TestClient) -> None:
    assert client.post("/trials", json=TRIAL).json() == {"trial": TRIAL, "rows": 0}
    seqs = [client.post("/effects", json=post(effect())).json()["sink_seq"] for _ in range(3)]
    assert seqs == [1, 2, 3]
    rows = client.get("/ledger", params=TRIAL).json()["rows"]
    assert [r["sink_seq"] for r in rows] == [1, 2, 3]
    verified = client.get("/ledger/verify", params=TRIAL).json()
    assert verified == {"trial": TRIAL, "ok": True, "rows": 3, "first_bad": None}


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
    assert client.get("/ledger", params=TRIAL).json()["rows"] == []


def test_a_restarted_sink_resumes_the_open_trial(tmp_path: Path) -> None:
    first = TestClient(create_app(tmp_path))
    first.post("/trials", json=TRIAL)
    first.post("/effects", json=post(effect()))
    second = TestClient(create_app(tmp_path))  # a new process over the same data directory
    assert second.get("/health").json()["trial"] == TRIAL
    assert second.post("/effects", json=post(effect())).json()["sink_seq"] == 2
    assert second.get("/ledger/verify", params=TRIAL).json()["ok"] is True


def test_switching_trials_keeps_each_ledger(client: TestClient) -> None:
    client.post("/trials", json=TRIAL)
    client.post("/effects", json=post(effect()))
    client.post("/trials", json={**TRIAL, "trial": 2})
    assert client.get("/ledger", params={**TRIAL, "trial": 2}).json()["rows"] == []
    assert client.post("/trials", json=TRIAL).json()["rows"] == 1


def test_a_request_for_another_trial_is_refused(client: TestClient) -> None:
    client.post("/trials", json={**TRIAL, "trial": 2})
    late = client.post("/effects", json=post(effect()))  # the previous trial's worker, late
    assert late.status_code == 409 and "open trial" in late.json()["detail"]
    counter = client.post("/attempts/next", json={"trial": TRIAL, "key": "k"})
    assert counter.status_code == 409
    assert client.get("/ledger", params={**TRIAL, "trial": 2}).json()["rows"] == []
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


def test_reads_name_their_trial(client: TestClient) -> None:
    client.post("/trials", json=TRIAL)
    client.post("/effects", json=post(effect()))
    other = {**TRIAL, "trial": 2}
    assert client.get("/ledger", params=other).status_code == 409
    assert client.get("/ledger/verify", params=other).status_code == 409
    assert client.get("/ledger").status_code == 422  # a read must say which trial
    assert len(client.get("/ledger", params=TRIAL).json()["rows"]) == 1


def test_a_conflicting_ledger_file_is_refused_and_the_open_trial_keeps_working(
    client: TestClient,
) -> None:
    client.post("/trials", json=TRIAL)
    client.post("/effects", json=post(effect()))
    client.post("/trials", json={**TRIAL, "trial": 2})
    # Same batch, cell, and trial as the first, another seed: the file belongs to the first.
    clash = client.post("/trials", json={**TRIAL, "seed": "s2"})
    assert clash.status_code == 409 and "another trial" in clash.json()["detail"]
    assert client.get("/health").json()["trial"] == {**TRIAL, "trial": 2}
    still = client.post("/effects", json=post(effect(), {**TRIAL, "trial": 2}))
    assert still.status_code == 200


class Refusing:
    """A sink client whose sink answers with an error, or not at all."""

    def __init__(self, status: int | None) -> None:
        self.status = status

    def post_effect(self, trial: Any, effect: Any) -> dict[str, Any]:
        from crashcourt.sink.client import SinkError

        raise SinkError("refused" if self.status else "no answer", self.status)


@pytest.mark.parametrize(("status", "recorded"), [(409, "fail"), (422, "fail"), (None, "info")])
def test_a_refused_effect_is_a_failure_and_an_unanswered_one_is_unknown(
    tmp_path: Path, status: int | None, recorded: str
) -> None:
    from crashcourt.history.reader import read_history
    from crashcourt.history.writer import HistoryWriter, RunIdentity
    from crashcourt.sink.client import SinkError, record_effect
    from crashcourt.sink.ledger import EffectRequest

    run = RunIdentity("DEMO-b", "DEMO-c", 1, "s")
    path = tmp_path / "w.jsonl"
    with (
        HistoryWriter(path, run=run, proc="worker-1", pid=4242) as history,
        pytest.raises(SinkError),
    ):
        record_effect(history, Refusing(status), EffectRequest(**effect()))  # type: ignore[arg-type]
    assert read_history(path).events[-1].status == recorded


def test_a_garbled_answer_is_a_sink_error() -> None:
    import http.server
    import threading

    from crashcourt.sink.client import SinkClient, SinkError

    class Garbled(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not json")

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Garbled)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(SinkError, match="JSONDecodeError"):
            SinkClient(f"http://127.0.0.1:{server.server_address[1]}").health()
    finally:
        server.shutdown()
        server.server_close()


def test_a_trial_that_cannot_be_resumed_leaves_the_sink_up_with_no_trial(tmp_path: Path) -> None:
    import sqlite3

    first = TestClient(create_app(tmp_path))
    first.post("/trials", json=TRIAL)
    ledger = next(tmp_path.rglob("*.sqlite"))
    with sqlite3.connect(ledger) as conn:  # as if an older sink had written it
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'ledger_version'")
    second = TestClient(create_app(tmp_path))  # restarts over the same data
    health = second.get("/health").json()
    assert health["trial"] is None and "ledger version 1" in health["resume_error"]
    second.post("/trials", json={**TRIAL, "trial": 2})
    assert second.get("/health").json()["resume_error"] is None


def _serve(answer: bytes, status: int = 200) -> tuple[Any, str]:
    import http.server
    import threading

    class Fixed(http.server.BaseHTTPRequestHandler):
        def _reply(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(answer)

        def do_GET(self) -> None:
            self._reply()

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._reply()

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Fixed)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.mark.parametrize("answer", [b"{}", b"[1]", b'{"sink_seq": "x"}', b'{"sink_seq": 0}'])
def test_an_answer_without_a_row_is_an_unknown_outcome(tmp_path: Path, answer: bytes) -> None:
    from crashcourt.history.reader import read_history
    from crashcourt.history.writer import HistoryWriter, RunIdentity
    from crashcourt.sink.client import SinkClient, SinkError, record_effect
    from crashcourt.sink.ledger import EffectRequest

    server, url = _serve(answer)
    run = RunIdentity("DEMO-b", "DEMO-c", 1, "s")
    path = tmp_path / "w.jsonl"
    try:
        with (
            HistoryWriter(path, run=run, proc="worker-1", pid=4242) as history,
            pytest.raises(SinkError),
        ):
            record_effect(history, SinkClient(url), EffectRequest(**effect()))
    finally:
        server.shutdown()
        server.server_close()
    events = read_history(path).events
    assert [(e.kind, e.status) for e in events] == [
        ("effect.request", None),
        ("effect.response", "info"),
    ]


def test_the_client_ignores_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from crashcourt.sink.client import SinkClient

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # nothing listens there
    monkeypatch.setenv("NO_PROXY", "")
    server, url = _serve(b'{"status": "ok"}')
    try:
        assert SinkClient(url).health() == {"status": "ok"}
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("name", ["..", ".hidden", "a/b", "a\\b", ""])
def test_batch_and_cell_ids_cannot_leave_the_data_directory(client: TestClient, name: str) -> None:
    assert client.post("/trials", json={**TRIAL, "batch": name}).status_code == 422
    assert client.post("/trials", json={**TRIAL, "cell": name}).status_code == 422
