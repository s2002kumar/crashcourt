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

    Each call waits up to `wait_ms` for its backend to exit, so a backend counts only once it is
    gone (with no timeout, the function returns true as soon as the signal is sent). The client's
    next statement fails with AdminShutdown (SQLSTATE 57P01). Ending no backend raises
    FaultError: the fault did nothing, so the trial cannot count it as injected.
    """
    if not application_name:
        raise FaultError("terminate_backends needs an application_name to target")

    def apply() -> Data:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
            # With a timeout, pg_terminate_backend returns true only once the backend has
            # exited, and false when it is already gone or outlives the wait; count the trues.
            row = conn.execute(
                "SELECT count(*) FROM ("
                " SELECT pg_terminate_backend(pid, %s) AS ended FROM pg_stat_activity"
                " WHERE application_name = %s AND pid <> pg_backend_pid()"
                ") AS t WHERE ended",
                (wait_ms, application_name),
            ).fetchone()
        terminated = int(row[0]) if row else 0
        if terminated == 0:
            raise FaultError(f"no backend with application_name {application_name!r} ended")
        return {"terminated": terminated}

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
