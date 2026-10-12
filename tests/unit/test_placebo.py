"""The placebo toxic's install, read-back, and removal, and the runner's fences and records for the
instrument checks: saver mutants run only where their trials cannot be taken for evidence, and a
redelivery is noted in the trial record."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from crashcourt.faults.toxiproxy import Proxy, Toxic, ToxiproxyClient, ToxiproxyError
from crashcourt.orchestrator import placebo
from crashcourt.orchestrator.dispatcher import EngineReport, OpReport, WorkerReport
from crashcourt.orchestrator.runner import RunOptions, RunRefused, assess, require_mutant_allowed
from crashcourt.orchestrator.topology import routes
from crashcourt.spec.scenario import PlaceboToxic

from .test_runner import TrialFiles, scenario

ROUTES = routes(["w1", "w2"], "host=127.0.0.1 port=55432 user=u dbname=d", (15433, 15434))
DECLARED = (
    PlaceboToxic(worker="w1", type="latency", latency_ms=0, jitter_ms=0),
    PlaceboToxic(worker="w2", type="latency", stream="upstream", latency_ms=0, jitter_ms=0),
)


class FakeToxiproxy:
    def __init__(self, *, refuse: str | None = None, alter: bool = False, keep: bool = False):
        self.toxics: dict[str, list[Toxic]] = {r.proxy: [] for r in ROUTES.values()}
        self.refuse, self.alter, self.keep = refuse, alter, keep

    def add_toxic(self, proxy: str, toxic: Toxic) -> Toxic:
        if proxy == self.refuse:
            raise ToxiproxyError(f"POST /proxies/{proxy}/toxics: HTTP 409")
        stored = (
            Toxic(toxic.name, toxic.type, toxic.stream, {"latency": 5}) if self.alter else toxic
        )
        self.toxics[proxy].append(stored)
        return stored

    def remove_toxic(self, proxy: str, name: str) -> None:
        if not self.keep:
            self.toxics[proxy] = [t for t in self.toxics[proxy] if t.name != name]

    def proxy(self, name: str) -> Proxy:
        return Proxy(name, "0.0.0.0:1", "postgres:5432", True, tuple(self.toxics[name]))


def client(fake: FakeToxiproxy) -> ToxiproxyClient:
    return cast(ToxiproxyClient, fake)


def test_each_declared_toxic_is_installed_confirmed_and_removed() -> None:
    fake = FakeToxiproxy()
    records, problems = placebo.install(client(fake), ROUTES, DECLARED)
    assert problems == []
    assert fake.toxics["postgres-w1"] == [
        Toxic(
            "crashcourt_placebo_w1_downstream", "latency", "downstream", {"latency": 0, "jitter": 0}
        )
    ]
    assert [(r["proxy"], r["stream"], r["installed"], r["removed"]) for r in records] == [
        ("postgres-w1", "downstream", True, False),
        ("postgres-w2", "upstream", True, False),
    ]
    assert placebo.remove(client(fake), records) == []
    assert all(r["removed"] for r in records)
    assert fake.toxics == {"postgres-w1": [], "postgres-w2": []}


def test_a_toxic_that_did_not_go_in_as_declared_is_a_problem() -> None:
    records, problems = placebo.install(
        client(FakeToxiproxy(refuse="postgres-w2")), ROUTES, DECLARED
    )
    assert problems == [
        "the placebo toxic on postgres-w2 was not installed: POST /proxies/postgres-w2/toxics: "
        "HTTP 409"
    ]
    assert [r["installed"] for r in records] == [True, False]
    altered = FakeToxiproxy(alter=True)
    _, problems = placebo.install(client(altered), ROUTES, DECLARED[:1])
    assert len(problems) == 1 and "not the placebo" in problems[0]


def test_a_toxic_left_behind_is_a_problem() -> None:
    fake = FakeToxiproxy(keep=True)
    records, _ = placebo.install(client(fake), ROUTES, DECLARED[:1])
    assert placebo.remove(client(fake), records) == [
        "the placebo toxic is still on postgres-w1 after its removal"
    ]
    assert records[0]["removed"] is False


def test_a_saver_mutant_never_runs_canonical_or_into_results(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    ordinary = RunOptions(trials=1, out=tmp_path / "runs", saver_mutant="M3")
    require_mutant_allowed(ordinary, root)
    with pytest.raises(RunRefused, match="canonical"):
        require_mutant_allowed(
            RunOptions(trials=1, out=tmp_path, canonical=True, saver_mutant="M3"), root
        )
    with pytest.raises(RunRefused, match="never written under"):
        require_mutant_allowed(
            RunOptions(trials=1, out=root / "results" / "c5", saver_mutant="M3"), root
        )


def test_a_redelivery_and_its_stale_run_are_noted_in_the_trial_record(tmp_path: Path) -> None:
    files = TrialFiles(tmp_path)
    files.normal()
    engine: Any = EngineReport(
        ops={
            "op-1": OpReport(
                "w1",
                "b-1/smoke/1/t1",
                True,
                "ok",
                None,
                None,
                redelivered_to="w2",
                stale=("w1 answered ok",),
            )
        },
        workers={"w1": WorkerReport(200, 0, True, None, False)},
        fired_steps=[],
        unfired_steps=[],
    )
    result = assess(scenario(), engine, files.dir, [])
    assert result.valid, result.reasons
    assert result.observations == [
        "op op-1 was redelivered to w2",
        "op op-1's stale run: w1 answered ok",
    ]
