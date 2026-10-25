"""Every fault primitive against the compose services, once (CI also runs the 20-trial self-test).

Signal faults need POSIX; on Linux (CI) the signal check must pass, not skip.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest

from crashcourt.faults import db, selftest
from crashcourt.faults.base import FaultError
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.probes import (
    DEFAULT_PG_DSN,
    DEFAULT_PG_PROXY_DSN,
    DEFAULT_TOXIPROXY_URL,
    SystemProbes,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def wait_until_postgres_is_healthy(timeout_s: float = 90) -> None:
    """The restart check returns once Postgres accepts connections, but compose reports the
    container `starting` until its next healthcheck passes, and doctor (so every later test that
    runs a batch) accepts only `healthy`. Leave the stack as the suite found it, as CI does after
    the 20-trial self-test."""
    probes = SystemProbes(ROOT)
    deadline = time.monotonic() + timeout_s
    while (state := probes.service_states().get("postgres")) != "healthy":
        if time.monotonic() > deadline:
            raise AssertionError(f"postgres is {state!r} {timeout_s} s after the restart check")
        time.sleep(0.5)


def test_one_selftest_trial_passes_every_check(tmp_path: Path) -> None:
    targets = selftest.prepare(
        os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN),
        os.environ.get("CRASHCOURT_PG_PROXY_DSN", DEFAULT_PG_PROXY_DSN),
        os.environ.get("CRASHCOURT_TOXIPROXY_URL", DEFAULT_TOXIPROXY_URL),
        ROOT,
    )
    try:
        trial = selftest.run_trial(targets, 1, tmp_path, restart=True)
    finally:
        targets.toxiproxy.reset()
        wait_until_postgres_is_healthy()
    statuses = {c.name: c.status for c in trial.checks}
    failed = {c.name: c.observed for c in trial.checks if c.status == "fail"}
    assert not failed, failed
    expected_skips = {"signals"} if sys.platform == "win32" else set()
    assert {name for name, status in statuses.items() if status == "skip"} == expected_skips


def test_terminating_no_backend_is_a_fault_error(tmp_path: Path) -> None:
    run = RunIdentity(batch="DEMO-faults", cell="DEMO-cell", trial=0, seed="s")
    path = tmp_path / "orchestrator.jsonl"
    with (
        HistoryWriter(path, run=run, proc="orchestrator") as history,
        pytest.raises(FaultError, match="no backend"),
    ):
        db.terminate_backends(
            history,
            os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN),
            "terminate-none",
            application_name=f"nobody-{uuid.uuid4().hex[:8]}",
        )
    heal = read_history(path).events[-1]
    assert (heal.kind, heal.status) == ("fault.heal", "fail")


def test_every_matching_backend_is_terminated_at_once(tmp_path: Path) -> None:
    dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
    app = f"victims-{uuid.uuid4().hex[:8]}"
    victims = [psycopg.connect(dsn, autocommit=True, application_name=app) for _ in range(3)]
    run = RunIdentity(batch="DEMO-faults", cell="DEMO-cell", trial=0, seed="s")
    path = tmp_path / "orchestrator.jsonl"
    try:
        with HistoryWriter(path, run=run, proc="orchestrator") as history:
            ended = db.terminate_backends(history, dsn, "terminate-3", application_name=app)
        for victim in victims:
            with pytest.raises(psycopg.errors.AdminShutdown):
                victim.execute("SELECT 1")
    finally:
        for victim in victims:
            victim.close()
    heal = read_history(path).events[-1]
    assert ended == 3
    assert (heal.status, heal.data) == ("ok", {"matched": 3, "terminated": 3})
