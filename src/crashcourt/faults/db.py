"""Database faults: backend termination and a fast restart of the target container.

Both act through the direct admin connection or the Docker CLI, never through Toxiproxy, and
only against the compose project Crashcourt created.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import psycopg

from crashcourt.faults.base import Data, FaultError, inject_instant
from crashcourt.history.writer import HistoryWriter

COMPOSE_PROJECT = "crashcourt"
TARGET_SERVICE = "postgres"


def terminate_backends(
    history: HistoryWriter,
    admin_dsn: str,
    fault_id: str,
    *,
    application_name: str,
    wait_ms: int = 5000,
) -> int:
    """`pg_terminate_backend` every backend with this application_name; returns how many ended.

    One scan signals every matching backend at once, so the fault is a single instant. Then it
    waits up to `wait_ms` until every signalled backend has left pg_stat_activity (the function
    itself returns true as soon as the signal is sent, not when the backend exits). The client's
    next statement fails with AdminShutdown (SQLSTATE 57P01).

    FaultError, so the trial cannot count the fault as injected: no backend matched or could be
    signalled, or a signalled backend outlived the wait.
    """
    if not application_name:
        raise FaultError("terminate_backends needs an application_name to target")
    if wait_ms <= 0:
        raise FaultError("terminate_backends needs a positive wait_ms to see the backends end")

    def apply() -> Data:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT pid, pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE application_name = %s AND pid <> pg_backend_pid()",
                (application_name,),
            ).fetchall()
            signalled = sorted(int(pid) for pid, sent in rows if sent)
            seen: Data = {"matched": len(rows), "signalled": len(signalled)}
            if not signalled:
                raise FaultError(
                    f"no backend with application_name {application_name!r} could be signalled"
                    f" ({len(rows)} matched)",
                    {**seen, "terminated": 0},
                )
            deadline = time.monotonic() + wait_ms / 1000
            alive = signalled
            while alive and time.monotonic() < deadline:
                time.sleep(0.01)
                # The application_name too, so a pid the server reused for a new backend
                # does not count as one that outlived the signal.
                alive = [
                    int(pid)
                    for (pid,) in conn.execute(
                        "SELECT pid FROM pg_stat_activity"
                        " WHERE pid = ANY(%s) AND application_name = %s",
                        (alive, application_name),
                    ).fetchall()
                ]
        if alive:
            raise FaultError(
                f"backends {alive} of {signalled} were still running {wait_ms} ms after the signal",
                {**seen, "terminated": len(signalled) - len(alive), "still_running": len(alive)},
            )
        return {"matched": len(rows), "terminated": len(signalled)}

    data: Data = {
        "fault": "terminate_backends",
        "application_name": application_name,
        "wait_ms": wait_ms,
    }
    observed = inject_instant(history, fault_id, data, apply)
    terminated = observed.get("terminated", 0)
    return terminated if isinstance(terminated, int) else 0


def _compose(root: Path, *args: str, timeout: float = 120) -> str:
    done = subprocess.run(
        ["docker", "compose", "-p", COMPOSE_PROJECT, *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return done.stdout.strip()


def _check_target_container(root: Path) -> str:
    container = _compose(root, "ps", "-q", TARGET_SERVICE)
    if not container:
        raise FaultError(f"no running {TARGET_SERVICE} container in project {COMPOSE_PROJECT}")
    label = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
            container,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if label != COMPOSE_PROJECT:
        raise FaultError(f"container {container} belongs to project {label!r}, not ours")
    return container


def restart_database(
    history: HistoryWriter, admin_dsn: str, fault_id: str, *, root: Path, ready_timeout: float = 60
) -> Data:
    """Fast shutdown and restart of the target container; returns when it accepts connections
    again. The postgres image stops with SIGINT, Postgres's fast shutdown mode."""

    def apply() -> Data:
        container = _check_target_container(root)
        started = time.monotonic()
        _compose(root, "restart", TARGET_SERVICE)
        restarted = time.monotonic()
        while True:
            try:
                with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=2) as conn:
                    conn.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() - restarted > ready_timeout:
                    raise FaultError(
                        f"{TARGET_SERVICE} still refusing connections {ready_timeout} s later"
                    ) from None
                time.sleep(0.1)
        return {
            "container": container[:12],
            "restart_s": round(restarted - started, 3),
            "ready_s": round(time.monotonic() - restarted, 3),
        }

    return inject_instant(history, fault_id, {"fault": "restart_database"}, apply)
