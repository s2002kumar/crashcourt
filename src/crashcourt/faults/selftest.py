"""`crashcourt faults selftest`: check each fault primitive does what the fault model assumes.

Every network check runs one INSERT through the proxy while a direct connection (the oracle
path) watches whether it committed. A check passes only when both the client's view and the
database agree with the documented semantics (crashcourt.faults.toxiproxy). Run it on the
compose services; T-05's gate is 20 of 20 trials on Linux CI.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import psycopg
from psycopg.conninfo import conninfo_to_dict

from crashcourt.faults import db, signals
from crashcourt.faults.base import ActiveFault, FaultError
from crashcourt.faults.toxiproxy import (
    HOLD_MS,
    HOLD_TOXIC,
    LOOPBACK_HOSTS,
    POSTGRES_PROXY,
    Toxic,
    ToxiproxyClient,
    ToxiproxyError,
    add_latency,
    drop_responses,
    ensure_postgres_proxy,
    hold_responses,
    reset_connections,
    toxic_fault,
)
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity

Status = Literal["pass", "fail", "skip"]
TABLE = "crashcourt_fault_selftest"
WAIT_S = 5.0
LATENCY_MS = 200
#: Every check, in report order. `restart_database` is omitted under --no-restart.
CHECKS = (
    "downstream_drop_is_lost_ack",
    "upstream_drop_never_reaches_server",
    "downstream_hold_is_delayed_ack",
    "downstream_latency_delays_response",
    "reset_downstream_after_commit",
    "reset_upstream_before_execute",
    "new_toxic_on_held_stream_releases_hold",
    "terminate_backend",
    "restart_database",
    "signals",
    "inject_heal_pairs",
)


@dataclass
class Check:
    name: str
    status: Status
    observed: dict[str, object] = field(default_factory=dict[str, object])


@dataclass
class Trial:
    number: int
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)


@dataclass(frozen=True)
class Targets:
    direct_dsn: str
    proxied_dsn: str
    toxiproxy: ToxiproxyClient
    root: Path


def _error(exc: BaseException) -> str:
    sqlstate = getattr(exc, "sqlstate", None)
    first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}{f' [{sqlstate}]' if sqlstate else ''}: {first}"[:160]


def _committed(dsn: str, key: str) -> bool:
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        row = conn.execute(f"SELECT count(*) FROM {TABLE} WHERE id = %s", (key,)).fetchone()
    return bool(row and row[0] == 1)


def _wait_until(predicate: Callable[[], bool], timeout: float = WAIT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class _Insert:
    """One INSERT through the proxy on its own thread, so the test can watch it block."""

    def __init__(self, dsn: str) -> None:
        self.key = f"selftest-{uuid.uuid4()}"
        self.conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
        self.outcome: str | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.conn.execute(f"INSERT INTO {TABLE} (id) VALUES (%s)", (self.key,))
            self.outcome = "ok"
        except Exception as exc:  # noqa: BLE001 - the client's view is the observation
            self.outcome = _error(exc)

    def start(self) -> _Insert:
        self.thread.start()
        return self

    def done_within(self, timeout: float) -> bool:
        self.thread.join(timeout)
        return not self.thread.is_alive()

    def finish(self, timeout: float = WAIT_S) -> str | None:
        done = self.done_within(timeout)
        with contextlib.suppress(Exception):  # the connection may already be broken
            self.conn.close()
        return self.outcome if done else None


def _network_check(
    name: str,
    targets: Targets,
    fault: Callable[[], ActiveFault],
    *,
    expect_commit_while_waiting: bool,
    expect_client: Literal["ok", "error"],
    expect_committed: bool,
) -> Check:
    targets.toxiproxy.reset()
    insert = _Insert(targets.proxied_dsn)
    try:
        active = fault()
    except BaseException:
        insert.conn.close()
        raise
    insert.start()
    if expect_commit_while_waiting:
        committed_early = _wait_until(lambda: _committed(targets.direct_dsn, insert.key))
    else:
        time.sleep(1.0)
        committed_early = _committed(targets.direct_dsn, insert.key)
    blocked = insert.thread.is_alive()
    active.heal()
    outcome = insert.finish()
    committed = _committed(targets.direct_dsn, insert.key)
    client_ok = outcome == "ok"
    ok = (
        committed_early == expect_commit_while_waiting
        and blocked
        and outcome is not None
        and client_ok == (expect_client == "ok")
        and committed == expect_committed
    )
    return Check(
        name,
        "pass" if ok else "fail",
        {
            "committed_while_client_waited": committed_early,
            "client_blocked_until_heal": blocked,
            "client_after_heal": outcome or "still blocked",
            "committed_final": committed,
        },
    )


def _latency_check(targets: Targets, history: HistoryWriter, trial: int) -> Check:
    """A downstream latency delays the response by at least that long; the row commits."""
    targets.toxiproxy.reset()
    key = f"selftest-{uuid.uuid4()}"
    with (
        psycopg.connect(targets.proxied_dsn, autocommit=True, connect_timeout=5) as conn,
        add_latency(history, targets.toxiproxy, f"latency-{trial}", LATENCY_MS),
    ):
        started = time.perf_counter()
        try:
            conn.execute(f"INSERT INTO {TABLE} (id) VALUES (%s)", (key,))
            outcome = "ok"
        except psycopg.Error as exc:
            outcome = _error(exc)
        elapsed_ms = (time.perf_counter() - started) * 1000
    committed = _committed(targets.direct_dsn, key)
    ok = outcome == "ok" and elapsed_ms >= LATENCY_MS and committed
    return Check(
        "downstream_latency_delays_response",
        "pass" if ok else "fail",
        {
            "latency_ms": LATENCY_MS,
            "client": outcome,
            "elapsed_ms": round(elapsed_ms, 1),
            "committed": committed,
        },
    )


def _reset_check(
    name: str,
    targets: Targets,
    history: HistoryWriter,
    stream: Literal["upstream", "downstream"],
    trial: int,
) -> Check:
    targets.toxiproxy.reset()
    key = f"selftest-{uuid.uuid4()}"
    with (
        psycopg.connect(targets.proxied_dsn, autocommit=True, connect_timeout=5) as conn,
        reset_connections(history, targets.toxiproxy, f"{name}-{trial}", stream),
    ):
        try:
            conn.execute(f"INSERT INTO {TABLE} (id) VALUES (%s)", (key,))
            outcome = "ok"
        except psycopg.Error as exc:
            outcome = _error(exc)
    committed = _committed(targets.direct_dsn, key)
    expect_committed = stream == "downstream"
    ok = outcome != "ok" and committed == expect_committed
    return Check(name, "pass" if ok else "fail", {"client": outcome, "committed": committed})


def _hold_guard_check(targets: Targets, history: HistoryWriter, trial: int) -> Check:
    """A new toxic on the held stream releases the hold; one on the other stream does not.

    The fault API refuses to add any toxic while a hold is active; raw Toxiproxy shows why.
    """
    targets.toxiproxy.reset()
    insert = _Insert(targets.proxied_dsn)
    targets.toxiproxy.add_toxic(
        POSTGRES_PROXY, Toxic(HOLD_TOXIC, "latency", "downstream", {"latency": HOLD_MS})
    )
    insert.start()
    committed = _wait_until(lambda: _committed(targets.direct_dsn, insert.key))
    held = insert.thread.is_alive()
    refused = False
    try:
        toxic_fault(
            history,
            targets.toxiproxy,
            f"guard-{trial}",
            "latency",
            Toxic("guard", "latency", "upstream", {"latency": 1}),
            POSTGRES_PROXY,
        )
    except ToxiproxyError:
        refused = True
    targets.toxiproxy.add_toxic(
        POSTGRES_PROXY, Toxic("raw-up", "latency", "upstream", {"latency": 1})
    )
    upstream_add_released = insert.done_within(1.0)
    targets.toxiproxy.add_toxic(
        POSTGRES_PROXY, Toxic("raw-down", "latency", "downstream", {"latency": 1})
    )
    downstream_add_released = insert.finish(timeout=2.0) == "ok"
    targets.toxiproxy.reset()
    ok = committed and held and refused and not upstream_add_released and downstream_add_released
    return Check(
        "new_toxic_on_held_stream_releases_hold",
        "pass" if ok else "fail",
        {
            "api_refused_second_toxic": refused,
            "upstream_add_released_hold": upstream_add_released,
            "downstream_add_released_hold": downstream_add_released,
        },
    )


def _terminate_check(targets: Targets, history: HistoryWriter, trial: int) -> Check:
    app = f"selftest-victim-{uuid.uuid4().hex[:8]}"
    victim = psycopg.connect(targets.direct_dsn, autocommit=True, application_name=app)
    try:
        count = db.terminate_backends(
            history, targets.direct_dsn, f"terminate-{trial}", application_name=app
        )
        try:
            victim.execute("SELECT 1")
            outcome = "ok"
        except psycopg.Error as exc:
            outcome = _error(exc)
    finally:
        victim.close()
    ok = count == 1 and "57P01" in outcome
    return Check(
        "terminate_backend",
        "pass" if ok else "fail",
        {"terminated": count, "client_next_query": outcome},
    )


def _restart_check(targets: Targets, history: HistoryWriter, trial: int) -> Check:
    key = f"selftest-{uuid.uuid4()}"
    with psycopg.connect(targets.direct_dsn, autocommit=True) as conn:
        conn.execute(f"INSERT INTO {TABLE} (id) VALUES (%s)", (key,))
    held = psycopg.connect(targets.direct_dsn, autocommit=True)
    try:
        timing = db.restart_database(
            history, targets.direct_dsn, f"restart-{trial}", root=targets.root
        )
        try:
            held.execute("SELECT 1")
            outcome = "ok"
        except psycopg.Error as exc:
            outcome = _error(exc)
    finally:
        held.close()
    survived = _committed(targets.direct_dsn, key)
    # On Linux, fast shutdown ends open connections with 57P01. Through Docker Desktop's
    # networking on Windows the client sees a socket abort instead.
    ended_cleanly = sys.platform == "win32" or "57P01" in outcome
    ok = survived and outcome != "ok" and ended_cleanly
    return Check(
        "restart_database",
        "pass" if ok else "fail",
        {"acked_row_survived": survived, "open_connection": outcome, **timing},
    )


HEARTBEAT = """
import sys, time
path = sys.argv[1]
n = 0
while True:
    n += 1
    with open(path, "w") as f:
        f.write(str(n))
    time.sleep(0.01)
"""


def _beats(path: Path) -> int:
    try:
        return int(path.read_text() or 0)
    except (OSError, ValueError):
        return 0


def _signal_check(history: HistoryWriter, trial: int, work: Path) -> Check:
    if sys.platform == "win32":
        return Check("signals", "skip", {"reason": "SIGSTOP and SIGCONT need a POSIX host"})
    beat = work / f"beat-{trial}"
    child = subprocess.Popen([sys.executable, "-c", HEARTBEAT, str(beat)])
    observed: dict[str, object] = {}
    try:
        _wait_until(lambda: _beats(beat) > 5)
        with signals.stop(history, child, f"stop-{trial}"):
            time.sleep(0.1)
            frozen = _beats(beat)
            time.sleep(0.4)
            observed["advanced_while_stopped"] = _beats(beat) - frozen
        observed["resumed"] = _wait_until(lambda: _beats(beat) > frozen + 5)
        observed["sigterm_returncode"] = signals.terminate(history, child, f"term-{trial}")
    finally:
        if child.poll() is None:
            child.kill()
    victim = subprocess.Popen([sys.executable, "-c", HEARTBEAT, str(beat)])
    observed["sigkill_returncode"] = signals.kill(history, victim, f"kill-{trial}")
    ok = (
        observed["advanced_while_stopped"] == 0
        and observed["resumed"] is True
        and observed["sigterm_returncode"] == -15
        and observed["sigkill_returncode"] == -9
    )
    return Check("signals", "pass" if ok else "fail", observed)


def _history_check(path: Path) -> Check:
    events = read_history(path).events
    injects = [e for e in events if e.kind == "fault.inject"]
    heals = [e for e in events if e.kind == "fault.heal"]
    paired = all(
        sum(1 for h in heals if h.fault_id == i.fault_id and h.seq > i.seq) == 1 for i in injects
    )
    failed = sum(1 for h in heals if h.status != "ok")
    ok = bool(injects) and paired and len(injects) == len(heals) and failed == 0
    return Check(
        "inject_heal_pairs",
        "pass" if ok else "fail",
        {"injects": len(injects), "heals": len(heals), "failed_heals": failed},
    )


def _guarded(name: str, check: Callable[[], Check]) -> Check:
    """Run a check; a primitive that raises (FaultError, a database error) fails the check."""
    try:
        return check()
    except Exception as exc:  # noqa: BLE001 - reported as the check's observation
        return Check(name, "fail", {"raised": _error(exc)})


def run_trial(targets: Targets, number: int, history_dir: Path, *, restart: bool) -> Trial:
    path = history_dir / f"selftest-trial-{number:02d}.jsonl"
    run = RunIdentity(batch="selftest", cell="fault-selftest", trial=number, seed="0")
    checks: list[Check] = []
    with HistoryWriter(path, run=run, proc="orchestrator") as history:
        tp = targets.toxiproxy
        planned: list[tuple[str, Callable[[], Check]]] = [
            (
                "downstream_drop_is_lost_ack",
                lambda: _network_check(
                    "downstream_drop_is_lost_ack",
                    targets,
                    lambda: drop_responses(history, tp, f"drop-{number}"),
                    expect_commit_while_waiting=True,
                    expect_client="error",
                    expect_committed=True,
                ),
            ),
            (
                "upstream_drop_never_reaches_server",
                lambda: _network_check(
                    "upstream_drop_never_reaches_server",
                    targets,
                    lambda: toxic_fault(
                        history,
                        tp,
                        f"drop-up-{number}",
                        "drop_requests",
                        Toxic(f"drop-up-{number}", "timeout", "upstream", {"timeout": 0}),
                        POSTGRES_PROXY,
                    ),
                    expect_commit_while_waiting=False,
                    expect_client="error",
                    expect_committed=False,
                ),
            ),
            (
                "downstream_hold_is_delayed_ack",
                lambda: _network_check(
                    "downstream_hold_is_delayed_ack",
                    targets,
                    lambda: hold_responses(history, tp, f"hold-{number}"),
                    expect_commit_while_waiting=True,
                    expect_client="ok",
                    expect_committed=True,
                ),
            ),
            (
                "downstream_latency_delays_response",
                lambda: _latency_check(targets, history, number),
            ),
            (
                "reset_downstream_after_commit",
                lambda: _reset_check(
                    "reset_downstream_after_commit", targets, history, "downstream", number
                ),
            ),
            (
                "reset_upstream_before_execute",
                lambda: _reset_check(
                    "reset_upstream_before_execute", targets, history, "upstream", number
                ),
            ),
            (
                "new_toxic_on_held_stream_releases_hold",
                lambda: _hold_guard_check(targets, history, number),
            ),
            ("terminate_backend", lambda: _terminate_check(targets, history, number)),
        ]
        if restart:
            planned.append(("restart_database", lambda: _restart_check(targets, history, number)))
        planned.append(("signals", lambda: _signal_check(history, number, history_dir)))
        checks += [_guarded(name, check) for name, check in planned]
    checks.append(_guarded("inject_heal_pairs", lambda: _history_check(path)))
    return Trial(number, checks)


def require_local_dsn(dsn: str) -> None:
    """The self-test's databases must be on this host, like the Toxiproxy API."""
    params = conninfo_to_dict(dsn)
    hosts = [
        host
        for key in ("host", "hostaddr")
        for host in str(params.get(key) or "").split(",")
        if host
    ]
    if not hosts or any(h not in LOOPBACK_HOSTS and not h.startswith("/") for h in hosts):
        shown = params.get("host") or params.get("hostaddr")
        raise FaultError(f"refusing a database that is not on this host: host {shown!r}")


def require_local_connection(conn: psycopg.Connection[object]) -> None:
    """Where a connection actually went, after environment variables and service files: a
    loopback address, or a Unix socket directory."""
    hostaddr, host = conn.info.hostaddr, conn.info.host
    if hostaddr not in {"127.0.0.1", "::1"} and not (not hostaddr and host.startswith("/")):
        raise FaultError(f"refusing a database that is not on this host: {host} ({hostaddr})")


def prepare(direct_dsn: str, proxied_dsn: str, toxiproxy_url: str, root: Path) -> Targets:
    """Ensure the Postgres proxy and the self-test table exist."""
    require_local_dsn(direct_dsn)
    require_local_dsn(proxied_dsn)
    targets = Targets(direct_dsn, proxied_dsn, ToxiproxyClient(toxiproxy_url), root)
    ensure_postgres_proxy(targets.toxiproxy)
    with psycopg.connect(proxied_dsn, autocommit=True, connect_timeout=5) as conn:
        require_local_connection(conn)
    with psycopg.connect(direct_dsn, autocommit=True) as conn:
        require_local_connection(conn)
        conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (id text PRIMARY KEY)")
    return targets


def run_selftest(
    *,
    trials: int,
    direct_dsn: str,
    proxied_dsn: str,
    toxiproxy_url: str,
    root: Path,
    history_dir: Path | None = None,
    restart: bool = True,
    progress: Callable[[Trial], None] | None = None,
) -> list[Trial]:
    targets = prepare(direct_dsn, proxied_dsn, toxiproxy_url, root)
    out = history_dir or Path(tempfile.mkdtemp(prefix="crashcourt-selftest-"))
    out.mkdir(parents=True, exist_ok=True)
    results: list[Trial] = []
    try:
        for number in range(1, trials + 1):
            trial = run_trial(targets, number, out, restart=restart)
            results.append(trial)
            if progress is not None:
                progress(trial)
    finally:
        targets.toxiproxy.reset()
    return results


def report(results: list[Trial]) -> str:
    """Markdown: pass counts per check and the observations from the first trial.

    The headline names every check that was skipped or left out, so a pass is never read as
    covering a check that did not run.
    """
    names = [c.name for c in results[0].checks] if results else []
    passed = sum(t.ok for t in results)
    skipped = [
        f"{name} in {count} of {len(results)} trials"
        for index, name in enumerate(names)
        if (count := sum(t.checks[index].status == "skip" for t in results))
    ]
    omitted = [name for name in CHECKS if name not in names]
    lines = [
        f"Trials passed: {passed} of {len(results)} (a trial passes when no check fails). "
        f"Skipped: {'; '.join(skipped) or 'none'}. Not run: {', '.join(omitted) or 'none'}.",
        "",
        "| Check | Pass | Fail | Skip | First trial's observation |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, name in enumerate(names):
        statuses = [t.checks[index].status for t in results]
        first = ", ".join(f"{k}={v}" for k, v in results[0].checks[index].observed.items())
        first = first.replace("|", "\\|")
        lines.append(
            f"| {name} | {statuses.count('pass')} | {statuses.count('fail')} | "
            f"{statuses.count('skip')} | {first} |"
        )
    failures = [(t.number, c) for t in results for c in t.checks if c.status == "fail"]
    if failures:
        lines += ["", "Failures:"]
        lines += [f"- trial {n}, {c.name}: {c.observed}" for n, c in failures]
    return "\n".join(lines) + "\n"
