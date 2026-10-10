from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crashcourt.faults import db
from crashcourt.faults.base import Data, FaultError
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.spec.events import Event


@pytest.mark.parametrize("wait_ms", [0, -1])
def test_termination_needs_a_positive_wait(tmp_path: Path, wait_ms: int) -> None:
    run = RunIdentity(batch="DEMO-faults", cell="DEMO-cell", trial=0, seed="s")
    with (
        HistoryWriter(tmp_path / "o.jsonl", run=run, proc="orchestrator") as history,
        pytest.raises(FaultError, match="positive wait_ms"),
    ):
        db.terminate_backends(
            history, "postgresql://u@127.0.0.1/db", "t-1", application_name="x", wait_ms=wait_ms
        )


T0 = datetime(2026, 10, 10, 3, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=5)
DSN = "postgresql://u@127.0.0.1/db"


class FakeDocker:
    """Stands in for the compose project's container and the server the admin DSN reaches."""

    def __init__(self, *, container_id: int, servers: list[tuple[int, datetime]]) -> None:
        self.container_id = container_id
        self.servers = servers
        self.restarts = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(db, "_check_target_container", lambda root: "c0ffee0123456789")
        monkeypatch.setattr(db, "_container_system_identifier", lambda c: self.container_id)
        monkeypatch.setattr(db, "_server", lambda dsn: self.servers.pop(0))
        monkeypatch.setattr(db, "_compose", self._compose)

    def _compose(self, root: Path, *args: str, timeout: float = 120) -> str:
        assert args == ("restart", db.TARGET_SERVICE)
        self.restarts += 1
        return ""


def _restart(tmp_path: Path) -> tuple[Data, Event]:
    run = RunIdentity(batch="DEMO-faults", cell="DEMO-cell", trial=0, seed="s")
    path = tmp_path / "o.jsonl"
    try:
        with HistoryWriter(path, run=run, proc="orchestrator") as history:
            observed = db.restart_database(history, DSN, "restart-1", root=tmp_path)
    finally:
        heal = read_history(path).events[-1]
    return observed, heal


def test_a_restart_is_confirmed_by_a_new_postmaster_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker = FakeDocker(container_id=7, servers=[(7, T0), (7, T1)])
    docker.install(monkeypatch)
    observed, heal = _restart(tmp_path)
    assert docker.restarts == 1
    assert heal.kind == "fault.heal" and heal.status == "ok"
    assert observed["postmaster_start_before"] == T0.isoformat()
    assert observed["postmaster_start_after"] == T1.isoformat()


def test_a_container_that_is_not_the_dsns_database_is_never_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another stack's database: the compose project's container is not the server the DSN
    # reaches, so restarting it would disturb that stack and leave the target untouched.
    docker = FakeDocker(container_id=8, servers=[(7, T0)])
    docker.install(monkeypatch)
    with pytest.raises(FaultError, match="nothing was restarted"):
        _restart(tmp_path)
    assert docker.restarts == 0
    heal = read_history(tmp_path / "o.jsonl").events[-1]
    assert heal.kind == "fault.heal" and heal.status == "fail"
    assert heal.data["container"] == "c0ffee012345"


@pytest.mark.parametrize(
    "after",
    [(7, T0), (9, T1)],
    ids=["same postmaster start", "another server answers"],
)
def test_a_restart_the_dsns_database_did_not_see_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: tuple[int, datetime]
) -> None:
    docker = FakeDocker(container_id=7, servers=[(7, T0), after])
    docker.install(monkeypatch)
    with pytest.raises(FaultError, match="did not restart"):
        _restart(tmp_path)
    heal = read_history(tmp_path / "o.jsonl").events[-1]
    assert heal.status == "fail"
    assert heal.data["postmaster_start_after"] == after[1].isoformat()
