"""Each worker's own route to the target database: one Toxiproxy proxy and port per worker.

A toxic acts on every connection through its proxy, so with a shared proxy a network fault meant
for one worker would hit both. Each worker therefore connects through `postgres-<worker>` on its
own port, with no toxics installed, so adding a network fault later changes nothing else about
the topology. The effect sink and the history files are never behind Toxiproxy.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from crashcourt.faults.toxiproxy import POSTGRES_UPSTREAM, ToxiproxyClient

#: Ports of w1's and w2's proxies; compose.yaml publishes them on 127.0.0.1.
DEFAULT_WORKER_PORTS: tuple[int, ...] = (15433, 15434)
PORTS_ENV = "CRASHCOURT_WORKER_PROXY_PORTS"
#: Workers reach their proxies here; Toxiproxy's ports are published on loopback only.
PROXY_HOST = "127.0.0.1"


class TopologyError(Exception):
    """The routes cannot be set up as the harness needs them."""


@dataclass(frozen=True)
class Route:
    worker: str
    proxy: str
    #: Where the proxy listens inside the Toxiproxy container.
    listen: str
    upstream: str
    port: int
    #: The worker's DSN: the target database, through this proxy.
    dsn: str

    def describe(self) -> dict[str, str | int]:
        return {
            "proxy": self.proxy,
            "listen": self.listen,
            "upstream": self.upstream,
            "port": self.port,
        }


def worker_ports(env: Mapping[str, str]) -> tuple[int, ...]:
    raw = env.get(PORTS_ENV)
    if not raw:
        return DEFAULT_WORKER_PORTS
    try:
        ports = tuple(int(part) for part in raw.split(","))
    except ValueError as exc:
        raise TopologyError(f"{PORTS_ENV}={raw!r} is not a comma-separated list of ports") from exc
    if len(set(ports)) != len(ports) or not all(0 < p < 65536 for p in ports):
        raise TopologyError(f"{PORTS_ENV}={raw!r}: ports must be distinct and valid")
    return ports


def routes(workers: Sequence[str], direct_dsn: str, ports: Sequence[int]) -> dict[str, Route]:
    """One route per worker, in order: w1 gets the first port, w2 the second."""
    if len(workers) > len(ports):
        raise TopologyError(f"{len(workers)} workers but only {len(ports)} proxy ports")
    found: dict[str, Route] = {}
    for worker, port in zip(workers, ports, strict=False):
        found[worker] = Route(
            worker=worker,
            proxy=f"postgres-{worker}",
            listen=f"0.0.0.0:{port}",
            upstream=POSTGRES_UPSTREAM,
            port=port,
            dsn=proxied_dsn(direct_dsn, port),
        )
    return found


def proxied_dsn(direct_dsn: str, port: int) -> str:
    """The target database's DSN with only its address replaced: loopback, this port.

    Every address parameter (host, hostaddr, port) is dropped first, so nothing in the original
    can send the connection anywhere but the proxy.
    """
    params = {
        key: value
        for key, value in conninfo_to_dict(direct_dsn).items()
        if key not in {"host", "hostaddr", "port"}
    }
    return make_conninfo("", **params, host=PROXY_HOST, port=str(port))


def ensure_routes(client: ToxiproxyClient, found: Mapping[str, Route]) -> None:
    """Create each worker's proxy if it is missing; refuse one that listens elsewhere."""
    for route in found.values():
        proxy = client.ensure_proxy(route.proxy, route.listen, route.upstream)
        if proxy.listen.rsplit(":", 1)[-1] != str(route.port):
            raise TopologyError(
                f"proxy {route.proxy} listens on {proxy.listen}, not port {route.port}; "
                "remove it or change the ports"
            )


def unclean(client: ToxiproxyClient, found: Mapping[str, Route]) -> list[str]:
    """Why a route is not a plain path right now: missing, disabled, or carrying a toxic."""
    problems: list[str] = []
    for route in found.values():
        proxy = client.proxy(route.proxy)
        if proxy is None:
            problems.append(f"proxy {route.proxy} is missing")
            continue
        if not proxy.enabled:
            problems.append(f"proxy {route.proxy} is disabled")
        if proxy.toxics:
            names = ", ".join(t.name for t in proxy.toxics)
            problems.append(f"proxy {route.proxy} has toxics installed: {names}")
    return problems


def ports_from_env() -> tuple[int, ...]:
    return worker_ports(os.environ)
