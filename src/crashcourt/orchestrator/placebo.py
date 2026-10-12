"""Whole-trial toxics: the placebo control's zero-latency toxic on each named worker's route (C3).

A scenario's `toxics` are installed on the workers' Toxiproxy routes before any worker starts and
removed after every worker has exited, so they cover the whole trial and are gone before the
post-trial snapshot (which must find no toxic installed). Each install and removal is read back
from Toxiproxy; anything that cannot be confirmed is a reason the trial is invalid.

They are not faults. Frozen checker v1 has no fault kind for a toxic that delays nothing, and a
`fault.inject` the scenario does not declare is INVALID (trigger 9), so nothing here writes to a
history: trial.json records what was installed, on which proxy, and what Toxiproxy said.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from crashcourt.faults.toxiproxy import Toxic, ToxiproxyClient, ToxiproxyError
from crashcourt.orchestrator.topology import Route
from crashcourt.spec.scenario import PlaceboToxic


def toxic_of(declared: PlaceboToxic) -> Toxic:
    """The Toxiproxy toxic a declaration installs; its name says what it is and where."""
    return Toxic(
        name=f"crashcourt_placebo_{declared.worker}_{declared.stream}",
        type=declared.type,
        stream=declared.stream,
        attributes={"latency": declared.latency_ms, "jitter": declared.jitter_ms},
    )


def _record(declared: PlaceboToxic, route: Route, toxic: Toxic) -> dict[str, Any]:
    return {
        "worker": declared.worker,
        "proxy": route.proxy,
        "name": toxic.name,
        "type": toxic.type,
        "stream": toxic.stream,
        "attributes": dict(toxic.attributes),
        "installed": False,
        "removed": False,
    }


def install(
    client: ToxiproxyClient, routes: Mapping[str, Route], declared: Sequence[PlaceboToxic]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Install every declared toxic and confirm each is on its proxy as declared. Returns the
    records for trial.json and the problems; a toxic that went in is recorded even when another
    failed, so that removal takes it out again."""
    records: list[dict[str, Any]] = []
    problems: list[str] = []
    for item in declared:
        route = routes[item.worker]
        toxic = toxic_of(item)
        record = _record(item, route, toxic)
        try:
            client.add_toxic(route.proxy, toxic)
            proxy = client.proxy(route.proxy)
        except ToxiproxyError as exc:
            problems.append(f"the placebo toxic on {route.proxy} was not installed: {exc}")
            records.append(record)
            continue
        found = [t for t in (proxy.toxics if proxy is not None else ()) if t.name == toxic.name]
        if found != [toxic]:
            shown = found[0] if found else "nothing"
            problems.append(f"Toxiproxy holds {shown} on {route.proxy}, not the placebo {toxic}")
        record["installed"] = bool(found)
        records.append(record)
    return records, problems


def remove(client: ToxiproxyClient, records: Sequence[dict[str, Any]]) -> list[str]:
    """Remove every toxic `install` put in, and confirm each proxy no longer holds it."""
    problems: list[str] = []
    for record in records:
        if not record["installed"]:
            continue
        proxy_name, name = str(record["proxy"]), str(record["name"])
        try:
            client.remove_toxic(proxy_name, name)
            proxy = client.proxy(proxy_name)
        except ToxiproxyError as exc:
            problems.append(f"the placebo toxic on {proxy_name} was not removed: {exc}")
            continue
        if proxy is not None and any(t.name == name for t in proxy.toxics):
            problems.append(f"the placebo toxic is still on {proxy_name} after its removal")
            continue
        record["removed"] = True
    return problems
