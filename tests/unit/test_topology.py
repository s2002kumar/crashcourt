"""Each worker's own Toxiproxy route: its own proxy and port, and a DSN that goes nowhere else."""

from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict

from crashcourt.faults.toxiproxy import Proxy, Toxic
from crashcourt.orchestrator import topology
from crashcourt.orchestrator.topology import Route, TopologyError

DIRECT = "postgresql://crashcourt:crashcourt@127.0.0.1:55432/crashcourt"


def test_each_worker_gets_its_own_proxy_and_port() -> None:
    routes = topology.routes(["w1", "w2"], DIRECT, topology.DEFAULT_WORKER_PORTS)
    assert {w: (r.proxy, r.listen, r.port) for w, r in routes.items()} == {
        "w1": ("postgres-w1", "0.0.0.0:15433", 15433),
        "w2": ("postgres-w2", "0.0.0.0:15434", 15434),
    }
    assert routes["w1"].upstream == "postgres:5432"
    assert conninfo_to_dict(routes["w2"].dsn) == {
        "user": "crashcourt",
        "password": "crashcourt",
        "dbname": "crashcourt",
        "host": "127.0.0.1",
        "port": "15434",
    }


def test_the_dsn_keeps_nothing_that_could_bypass_the_proxy() -> None:
    dsn = topology.proxied_dsn("host=db.example hostaddr=10.1.2.3 port=5432 dbname=x", 15433)
    assert conninfo_to_dict(dsn) == {"dbname": "x", "host": "127.0.0.1", "port": "15433"}


def test_ports_come_from_the_environment_when_set() -> None:
    assert topology.worker_ports({}) == (15433, 15434)
    assert topology.worker_ports({topology.PORTS_ENV: "15453,15454"}) == (15453, 15454)
    for bad in ("15453;15454", "1,1", "0,5", "x"):
        with pytest.raises(TopologyError):
            topology.worker_ports({topology.PORTS_ENV: bad})


def test_more_workers_than_ports_is_refused() -> None:
    with pytest.raises(TopologyError, match="2 workers but only 1"):
        topology.routes(["w1", "w2"], DIRECT, (15433,))


class FakeToxiproxy:
    def __init__(self, proxies: dict[str, Proxy]) -> None:
        self.proxies = proxies

    def proxy(self, name: str) -> Proxy | None:
        return self.proxies.get(name)

    def ensure_proxy(self, name: str, listen: str, upstream: str) -> Proxy:
        if name not in self.proxies:
            self.proxies[name] = Proxy(name, listen, upstream, True, ())
        return self.proxies[name]


def route(worker: str, port: int) -> Route:
    return Route(worker, f"postgres-{worker}", f"0.0.0.0:{port}", "postgres:5432", port, "dsn")


def test_missing_routes_are_created_and_a_misplaced_one_is_refused() -> None:
    fake = FakeToxiproxy({})
    topology.ensure_routes(fake, {"w1": route("w1", 15433)})  # type: ignore[arg-type]
    assert fake.proxies["postgres-w1"].listen == "0.0.0.0:15433"
    fake.proxies["postgres-w2"] = Proxy("postgres-w2", "[::]:16000", "postgres:5432", True, ())
    with pytest.raises(TopologyError, match="not port 15434"):
        topology.ensure_routes(fake, {"w2": route("w2", 15434)})  # type: ignore[arg-type]


def test_a_route_with_a_toxic_or_disabled_is_not_a_plain_path() -> None:
    toxic = Toxic("left_over", "latency", attributes={"latency": 5})
    fake = FakeToxiproxy(
        {
            "postgres-w1": Proxy("postgres-w1", "0.0.0.0:15433", "postgres:5432", False, ()),
            "postgres-w2": Proxy("postgres-w2", "0.0.0.0:15434", "postgres:5432", True, (toxic,)),
        }
    )
    routes = {"w1": route("w1", 15433), "w2": route("w2", 15434), "w3": route("w3", 15435)}
    assert topology.unclean(fake, routes) == [  # type: ignore[arg-type]
        "proxy postgres-w1 is disabled",
        "proxy postgres-w2 has toxics installed: left_over",
        "proxy postgres-w3 is missing",
    ]
