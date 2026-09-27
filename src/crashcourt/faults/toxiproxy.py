"""Toxiproxy: a typed client for the HTTP API, and the network faults built on it.

Semantics measured against Toxiproxy 2.12.0 and Postgres 18.6 (research/fault-selftest.md):

- `downstream` is server-to-client (responses), `upstream` is client-to-server (requests).
- `timeout` with timeout=0 *drops* data, and removing it closes the connection. On the downstream
  it makes a lost acknowledgment: the server commits, and the client gets a closed connection.
- `latency` holds data, and removing it forwards what it held. A very long downstream latency is
  therefore a deterministic delayed acknowledgment.
- Adding a toxic to the held stream interrupts a held `latency` toxic, which releases its data;
  a toxic on the other stream does not. The fault functions refuse to add any toxic while a hold
  is active, and `hold_responses` refuses to share a proxy with other toxics.
- `reset_peer` with timeout=0 closes the connection at the first data. On the downstream the
  server has already committed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from crashcourt.faults.base import ActiveFault, Data, FaultError, inject
from crashcourt.history.writer import HistoryWriter

Stream = Literal["upstream", "downstream"]

DEFAULT_URL = "http://127.0.0.1:8474"
#: The proxy the harness puts between workers and the target database (compose.yaml).
POSTGRES_PROXY = "postgres"
POSTGRES_LISTEN = "0.0.0.0:15432"
POSTGRES_UPSTREAM = "postgres:5432"
#: A downstream latency this long is a hold: nothing arrives until the toxic is removed.
HOLD_MS = 24 * 60 * 60 * 1000
HOLD_TOXIC = "crashcourt_hold"


class ToxiproxyError(FaultError):
    """The Toxiproxy API refused a request."""


@dataclass(frozen=True)
class Toxic:
    name: str
    type: str
    stream: Stream = "downstream"
    attributes: dict[str, int] = field(default_factory=dict[str, int])
    toxicity: float = 1.0

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "stream": self.stream,
            "toxicity": self.toxicity,
            "attributes": self.attributes,
        }

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Toxic:
        return cls(
            name=str(data["name"]),
            type=str(data["type"]),
            stream=cast(Stream, data.get("stream", "downstream")),
            attributes={str(k): int(v) for k, v in dict(data.get("attributes") or {}).items()},
            toxicity=float(data.get("toxicity", 1.0)),
        )


@dataclass(frozen=True)
class Proxy:
    name: str
    listen: str
    upstream: str
    enabled: bool
    toxics: tuple[Toxic, ...]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Proxy:
        return cls(
            name=str(data["name"]),
            listen=str(data["listen"]),
            upstream=str(data["upstream"]),
            enabled=bool(data.get("enabled", True)),
            toxics=tuple(Toxic.from_api(t) for t in data.get("toxics") or ()),
        )


class ToxiproxyClient:
    """The control API. Only a loopback address is accepted: the proxy is local by design."""

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 5.0) -> None:
        host = urlsplit(url).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ToxiproxyError(f"refusing a non-local Toxiproxy API: {url}")
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: object | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.url + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise ToxiproxyError(f"{method} {path}: HTTP {exc.code} {detail}") from exc
        except OSError as exc:  # URLError, refused connections, and timeouts
            raise ToxiproxyError(
                f"{method} {path}: Toxiproxy unreachable at {self.url}: {exc}"
            ) from exc
        return json.loads(text) if text.strip() else None

    def version(self) -> str:
        data = self._request("GET", "/version")
        if isinstance(data, dict):
            return str(cast("dict[str, object]", data).get("version"))
        return str(data)

    def proxy(self, name: str) -> Proxy | None:
        try:
            return Proxy.from_api(self._request("GET", f"/proxies/{name}"))
        except ToxiproxyError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def create_proxy(self, name: str, listen: str, upstream: str) -> Proxy:
        body = {"name": name, "listen": listen, "upstream": upstream, "enabled": True}
        return Proxy.from_api(self._request("POST", "/proxies", body))

    def ensure_proxy(self, name: str, listen: str, upstream: str) -> Proxy:
        """Create the proxy if it is missing. An existing one is kept (changing it would drop
        live connections) unless its upstream differs, which is an error."""
        existing = self.proxy(name)
        if existing is None:
            return self.create_proxy(name, listen, upstream)
        if existing.upstream != upstream:
            raise ToxiproxyError(f"proxy {name} points at {existing.upstream}, expected {upstream}")
        return existing

    def add_toxic(self, proxy: str, toxic: Toxic) -> Toxic:
        return Toxic.from_api(self._request("POST", f"/proxies/{proxy}/toxics", toxic.to_api()))

    def remove_toxic(self, proxy: str, name: str) -> None:
        self._request("DELETE", f"/proxies/{proxy}/toxics/{name}")

    def reset(self) -> None:
        """Enable every proxy and remove every toxic."""
        self._request("POST", "/reset")


def ensure_postgres_proxy(client: ToxiproxyClient) -> Proxy:
    return client.ensure_proxy(POSTGRES_PROXY, POSTGRES_LISTEN, POSTGRES_UPSTREAM)


def toxic_fault(
    history: HistoryWriter,
    client: ToxiproxyClient,
    fault_id: str,
    fault: str,
    toxic: Toxic,
    proxy: str,
) -> ActiveFault:
    current = client.proxy(proxy)
    if current is None:
        raise ToxiproxyError(f"no proxy named {proxy}")
    if any(t.name == HOLD_TOXIC for t in current.toxics):
        raise ToxiproxyError(f"proxy {proxy} has an active hold; adding a toxic would release it")
    data: Data = {
        "fault": fault,
        "proxy": proxy,
        "toxic": toxic.type,
        "stream": toxic.stream,
        "attributes": dict(toxic.attributes),
        "toxicity": toxic.toxicity,
    }

    def apply() -> Data:
        client.add_toxic(proxy, toxic)
        return {}

    def heal() -> Data:
        client.remove_toxic(proxy, toxic.name)
        return {"removed": toxic.name}

    return inject(history, fault_id, data, apply, heal)


def hold_responses(
    history: HistoryWriter, client: ToxiproxyClient, fault_id: str, proxy: str = POSTGRES_PROXY
) -> ActiveFault:
    """Delayed acknowledgment (F12): the server executes and commits, responses wait. Healing
    delivers them. Only valid on a proxy with no other toxics."""
    current = client.proxy(proxy)
    if current is not None and current.toxics:
        names = ", ".join(t.name for t in current.toxics)
        raise ToxiproxyError(f"proxy {proxy} already has toxics ({names}); a hold must be alone")
    toxic = Toxic(HOLD_TOXIC, "latency", "downstream", {"latency": HOLD_MS})
    return toxic_fault(history, client, fault_id, "hold_responses", toxic, proxy)


def drop_responses(
    history: HistoryWriter, client: ToxiproxyClient, fault_id: str, proxy: str = POSTGRES_PROXY
) -> ActiveFault:
    """Lost acknowledgment (F11): the server commits, responses are discarded, and healing
    closes the client's connection."""
    toxic = Toxic(f"{fault_id}_drop", "timeout", "downstream", {"timeout": 0})
    return toxic_fault(history, client, fault_id, "drop_responses", toxic, proxy)


def reset_connections(
    history: HistoryWriter,
    client: ToxiproxyClient,
    fault_id: str,
    stream: Stream = "downstream",
    proxy: str = POSTGRES_PROXY,
) -> ActiveFault:
    """Connection reset (F10): the first data on the stream closes the connection. Downstream,
    the request has already been executed; upstream, it never reaches the server."""
    toxic = Toxic(f"{fault_id}_reset", "reset_peer", stream, {"timeout": 0})
    return toxic_fault(history, client, fault_id, "reset_connections", toxic, proxy)


def add_latency(
    history: HistoryWriter,
    client: ToxiproxyClient,
    fault_id: str,
    latency_ms: int,
    jitter_ms: int = 0,
    stream: Stream = "downstream",
    proxy: str = POSTGRES_PROXY,
) -> ActiveFault:
    """Slow network (randomized F12 cells)."""
    toxic = Toxic(
        f"{fault_id}_latency", "latency", stream, {"latency": latency_ms, "jitter": jitter_ms}
    )
    return toxic_fault(history, client, fault_id, "latency", toxic, proxy)
