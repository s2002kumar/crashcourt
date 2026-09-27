"""The Toxiproxy client against a small in-process fake of its HTTP API."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from crashcourt.faults.toxiproxy import (
    HOLD_TOXIC,
    Toxic,
    ToxiproxyClient,
    ToxiproxyError,
    drop_responses,
    ensure_postgres_proxy,
    hold_responses,
)
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity


class FakeToxiproxy:
    def __init__(self) -> None:
        self.proxies: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, Any]] = []


def make_handler(state: FakeToxiproxy) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _reply(self, code: int, body: object | None = None) -> None:
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length)) if length else None

        def do_GET(self) -> None:
            state.requests.append(("GET", self.path, None))
            parts = self.path.strip("/").split("/")
            if parts == ["version"]:
                self._reply(200, {"version": "2.12.0"})
            elif len(parts) == 2 and parts[0] == "proxies" and parts[1] in state.proxies:
                self._reply(200, state.proxies[parts[1]])
            else:
                self._reply(404, {"error": "proxy not found", "status": 404})

        def do_POST(self) -> None:
            body = self._body()
            state.requests.append(("POST", self.path, body))
            parts = self.path.strip("/").split("/")
            if parts == ["proxies"]:
                state.proxies[body["name"]] = {**body, "toxics": []}
                self._reply(201, state.proxies[body["name"]])
            elif parts == ["reset"]:
                for proxy in state.proxies.values():
                    proxy["toxics"] = []
                self._reply(204)
            elif len(parts) == 3 and parts[2] == "toxics" and parts[1] not in state.proxies:
                self._reply(404, {"error": "proxy not found", "status": 404})
            elif len(parts) == 3 and parts[2] == "toxics":
                state.proxies[parts[1]]["toxics"].append(body)
                self._reply(200, body)
            else:
                self._reply(400, {"error": "bad request"})

        def do_DELETE(self) -> None:
            state.requests.append(("DELETE", self.path, None))
            parts = self.path.strip("/").split("/")
            toxics = state.proxies[parts[1]]["toxics"]
            state.proxies[parts[1]]["toxics"] = [t for t in toxics if t["name"] != parts[3]]
            self._reply(204)

    return Handler


@pytest.fixture
def fake() -> Iterator[tuple[FakeToxiproxy, ToxiproxyClient]]:
    state = FakeToxiproxy()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, ToxiproxyClient(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()


def writer(tmp_path: Path) -> HistoryWriter:
    run = RunIdentity(batch="DEMO-tp", cell="DEMO-cell", trial=0, seed="s")
    return HistoryWriter(tmp_path / "orchestrator.jsonl", run=run, proc="orchestrator")


def test_non_local_api_is_refused() -> None:
    with pytest.raises(ToxiproxyError, match="non-local"):
        ToxiproxyClient("http://toxiproxy.example.com:8474")


def test_version(fake: tuple[FakeToxiproxy, ToxiproxyClient]) -> None:
    assert fake[1].version() == "2.12.0"


def test_ensure_proxy_creates_once_and_keeps_an_existing_one(
    fake: tuple[FakeToxiproxy, ToxiproxyClient],
) -> None:
    state, client = fake
    ensure_postgres_proxy(client)
    ensure_postgres_proxy(client)
    posts = [r for r in state.requests if r[0] == "POST"]
    assert posts == [
        (
            "POST",
            "/proxies",
            {
                "name": "postgres",
                "listen": "0.0.0.0:15432",
                "upstream": "postgres:5432",
                "enabled": True,
            },
        )
    ]


def test_ensure_proxy_rejects_a_different_upstream(
    fake: tuple[FakeToxiproxy, ToxiproxyClient],
) -> None:
    _, client = fake
    client.create_proxy("postgres", "0.0.0.0:15432", "elsewhere:5432")
    with pytest.raises(ToxiproxyError, match="elsewhere"):
        ensure_postgres_proxy(client)


def test_drop_responses_adds_and_removes_its_toxic_with_events(
    fake: tuple[FakeToxiproxy, ToxiproxyClient], tmp_path: Path
) -> None:
    state, client = fake
    ensure_postgres_proxy(client)
    with writer(tmp_path) as h, drop_responses(h, client, "f-1"):
        [toxic] = state.proxies["postgres"]["toxics"]
        assert toxic == {
            "name": "f-1_drop",
            "type": "timeout",
            "stream": "downstream",
            "toxicity": 1.0,
            "attributes": {"timeout": 0},
        }
    assert state.proxies["postgres"]["toxics"] == []
    events = read_history(tmp_path / "orchestrator.jsonl").events
    assert [(e.kind, e.fault_id) for e in events] == [
        ("fault.inject", "f-1"),
        ("fault.heal", "f-1"),
    ]
    assert events[0].data["toxic"] == "timeout" and events[0].data["stream"] == "downstream"


def test_hold_must_be_alone_and_blocks_new_toxics(
    fake: tuple[FakeToxiproxy, ToxiproxyClient], tmp_path: Path
) -> None:
    state, client = fake
    ensure_postgres_proxy(client)
    with writer(tmp_path) as h:
        client.add_toxic("postgres", Toxic("other", "latency", "upstream", {"latency": 5}))
        with pytest.raises(ToxiproxyError, match="must be alone"):
            hold_responses(h, client, "f-hold")
        client.reset()
        with hold_responses(h, client, "f-hold"):
            assert [t["name"] for t in state.proxies["postgres"]["toxics"]] == [HOLD_TOXIC]
            with pytest.raises(ToxiproxyError, match="active hold"):
                drop_responses(h, client, "f-drop")
    kinds = [(e.kind, e.fault_id) for e in read_history(tmp_path / "orchestrator.jsonl").events]
    assert kinds == [("fault.inject", "f-hold"), ("fault.heal", "f-hold")]


def test_http_errors_become_toxiproxy_errors(fake: tuple[FakeToxiproxy, ToxiproxyClient]) -> None:
    with pytest.raises(ToxiproxyError, match="HTTP 404"):
        fake[1].add_toxic("missing", Toxic("x", "latency"))


def test_unreachable_api_is_a_toxiproxy_error() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # bound but not listening: connections are refused
        client = ToxiproxyClient(f"http://127.0.0.1:{port}", timeout=2)
        with pytest.raises(ToxiproxyError, match="unreachable"):
            client.version()
