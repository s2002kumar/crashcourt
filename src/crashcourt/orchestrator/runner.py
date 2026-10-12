"""`crashcourt run`: a batch of trials of one scenario, and the evidence each trial leaves.

Before the first trial the runner refuses to start unless:

- the host is Linux (workers need POSIX signals and Unix sockets; on Windows, use WSL2);
- the target database is on this host, unless `--i-own-this-database` says otherwise;
- doctor passes, so the batch has a complete manifest;
- the effect sink is healthy, has no test hook armed, and serves this checkout's source;
- each worker's Toxiproxy route exists, is enabled, and carries no toxic;
- in canonical mode, the checkout is clean and the sink's clock is within one second of this
  host's. Ordinary runs record a larger offset as a warning. The offset is never used to order
  events: the merge orders by causal edges, and wall clocks are never consulted.

Layout of one batch (`<out>/<batch>/`):

| File | Holds |
| --- | --- |
| manifest.json | doctor's manifest: clause registry sha256, sink clock offset, and the rest |
| batch.json | the v1 batch record: provenance run, manifest sha256, scenario content hash |
| scenario.json | the validated scenario in the canonical form its hash is taken over |
| scenario.yaml | the scenario file as given, when there is one |
| run.json | how the batch ran: mode, trials, seeds, routes, clock check, sink health |
| `<cell>/trial-NNNNN/` | every process's history, the merged history, worker logs, trial.json |
| | and the checker's evidence: ledger.json, snapshot.json, sink-health.json (`evidence`) |
| summary.json | every trial's validity and client outcomes |

Each trial must also start fresh: its sink ledger empty, and no row in the saver's tables for
any of its threads (thread ids embed the batch, so rows there mean a batch id was reused and the
trial would continue another one's threads). That check reads only row counts, before anything
is dispatched; nothing reads the target database to decide an outcome.

A trial is valid when every history parses, the merge finds no contradiction, every
`client.invoke` has exactly one terminal event, every scheduled step fired, every worker's exit is
explained, and nothing broke the protocol. Validity is about the harness, never about the target.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from crashcourt.doctor import format_report, run_doctor
from crashcourt.faults.toxiproxy import ToxiproxyClient
from crashcourt.history.merge import merge
from crashcourt.history.reader import HistoryError, read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.manifest import Manifest
from crashcourt.orchestrator import evidence, preregistered, topology
from crashcourt.orchestrator.dispatcher import EngineReport, Note, TrialEngine, describe_status
from crashcourt.orchestrator.workers import ProcessWorker, socket_directory
from crashcourt.probes import DEFAULT_PG_DSN, DEFAULT_SINK_URL, DEFAULT_TOXIPROXY_URL, SystemProbes
from crashcourt.sink.client import SinkClient, SinkError
from crashcourt.sink.ledger import LEDGER_VERSION, TrialId, ledger_events
from crashcourt.snapshot.dump import redact
from crashcourt.spec.content import canonical_json
from crashcourt.spec.events import Event
from crashcourt.spec.records import BatchRecord
from crashcourt.spec.scenario import (
    Exit,
    FailpointStep,
    Pause,
    Scenario,
    arm_of,
    scenario_sha256,
    trial_seed,
)
from crashcourt.worker.main import HARNESS_EXITS

#: Canonical batches refuse a sink clock further than this from the host's (one second).
CANONICAL_CLOCK_LIMIT_NS = 1_000_000_000
#: What the worker pools are opened with: the saver's documented settings.
POOL_SETTINGS: dict[str, JsonValue] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": "dict_row",
}
ORCHESTRATOR = "orchestrator"
SINK = "sink"
_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RunRefused(Exception):
    """The batch did not start; nothing was run."""


@dataclass(frozen=True)
class Settings:
    """Where the lab's services are: the same environment variables doctor reads."""

    root: Path
    direct_dsn: str
    toxiproxy_url: str
    sink_url: str
    worker_ports: tuple[int, ...]

    @classmethod
    def from_environment(cls, root: Path, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        return cls(
            root=root,
            direct_dsn=env.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN),
            toxiproxy_url=env.get("CRASHCOURT_TOXIPROXY_URL", DEFAULT_TOXIPROXY_URL),
            sink_url=env.get("CRASHCOURT_SINK_URL", DEFAULT_SINK_URL),
            worker_ports=topology.worker_ports(env),
        )


@dataclass(frozen=True)
class RunOptions:
    trials: int
    out: Path
    batch: str | None = None
    canonical: bool = False
    own_database: bool = False
    #: Instrument batch directories a pre-registered new-evidence cell rests on.
    instrument: tuple[Path, ...] = ()


@dataclass
class TrialResult:
    number: int
    directory: Path
    valid: bool
    reasons: list[str] = field(default_factory=list[str])
    observations: list[str] = field(default_factory=list[str])
    outcomes: Counter[str] = field(default_factory=Counter[str])


@dataclass(frozen=True)
class BatchSummary:
    batch: str
    directory: Path
    trials: list[TrialResult]
    #: Why a pre-registered batch's authorization was revoked mid-batch; empty when it was not.
    revoked: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.revoked and all(t.valid for t in self.trials)


# -- preflight --------------------------------------------------------------------------------


def require_linux(platform: str = sys.platform) -> None:
    if platform != "linux":
        hint = (
            "On Windows, run it under WSL2 (see the README)."
            if platform == "win32"
            else "Run it on Linux or inside a Linux container."
        )
        raise RunRefused(
            "crashcourt run needs a Linux host: workers are killed and stopped with POSIX "
            f"signals and driven over Unix sockets, and this host is {platform}. {hint}"
        )


def require_local_database(dsn: str, own_database: bool) -> None:
    """Refuse a target database that is not on this host, unless its owner says so."""
    from crashcourt.faults.base import FaultError
    from crashcourt.faults.selftest import require_local_dsn

    if own_database:
        return
    try:
        require_local_dsn(dsn)
    except FaultError as exc:
        raise RunRefused(
            f"{exc}; pass --i-own-this-database only if this database is yours to break"
        ) from exc


def check_clock(offsets: Mapping[str, int | None], canonical: bool) -> str | None:
    """Refuse (canonical) or warn about (ordinary) a sink clock too far from this host's."""
    problems: list[str] = []
    for where, offset in offsets.items():
        if offset is None:
            problems.append(f"{where}: no sink clock offset was measured")
        elif abs(offset) > CANONICAL_CLOCK_LIMIT_NS:
            problems.append(
                f"{where}: the sink's clock is {offset / 1e9:+.3f} s from this host's, beyond "
                f"the {CANONICAL_CLOCK_LIMIT_NS / 1e9:g} s limit"
            )
    if not problems:
        return None
    text = "; ".join(problems)
    if canonical:
        raise RunRefused(f"canonical batch refused: {text}")
    return f"{text} (recorded; never used to order events)"


def check_sink(health: Mapping[str, object], source: str) -> list[str]:
    problems: list[str] = []
    if health.get("status") != "ok":
        problems.append(f"status {health.get('status')}")
    if health.get("ledger_version") != LEDGER_VERSION:
        problems.append(f"ledger version {health.get('ledger_version')}, not {LEDGER_VERSION}")
    if health.get("test_hooks") is not False:
        problems.append("a test hook is armed")
    if health.get("resume_error"):
        problems.append(f"it could not resume its open trial: {health.get('resume_error')}")
    if health.get("source_sha256") != source:
        problems.append(
            f"it serves source {str(health.get('source_sha256'))[:12]}, but the batch's checkout "
            f"is {source[:12]}; rebuild it (`docker compose up -d --wait --build sink`)"
        )
    return problems


#: The saver's tables that hold a thread's rows.
THREAD_TABLES: tuple[str, ...] = ("checkpoints", "checkpoint_writes", "checkpoint_blobs")


def rows_for_threads(dsn: str, thread_ids: Sequence[str]) -> dict[str, int]:
    """How many rows each of the saver's tables already holds for these threads (direct path)."""
    import psycopg
    from psycopg import sql

    query = sql.SQL("SELECT count(*) FROM {} WHERE thread_id = ANY(%s)")
    found: dict[str, int] = {}
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        for table in THREAD_TABLES:
            row = conn.execute(query.format(sql.Identifier(table)), (list(thread_ids),)).fetchone()
            found[table] = int(row[0]) if row else 0
    return found


async def _setup_tables(dsn: str) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()


def _batch_id(given: str | None) -> str:
    batch = given or "b-" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    if not _BATCH_ID.fullmatch(batch):
        raise RunRefused(f"batch id {batch!r}: letters, digits, '.', '_', and '-' only")
    if batch.startswith("DEMO-"):
        raise RunRefused("DEMO- batch ids belong to fixtures, never to runs")
    return batch


def _shown(path: Path, root: Path) -> str:
    """A path as a record shows it: relative to the checkout when inside it, else its name only,
    so no batch file carries a home directory."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _write_json(path: Path, data: object) -> bytes:
    raw = (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


# -- one trial --------------------------------------------------------------------------------


@dataclass
class _Batch:
    scenario: Scenario
    settings: Settings
    batch: str
    directory: Path
    routes: dict[str, topology.Route]
    sink: SinkClient
    toxiproxy: ToxiproxyClient
    sink_source: str


async def run_trial(ctx: _Batch, number: int) -> TrialResult:
    scenario = ctx.scenario
    seed = trial_seed(scenario.seed, scenario.cell, number)
    identity = RunIdentity(ctx.batch, scenario.cell, number, seed)
    trial = TrialId(ctx.batch, scenario.cell, number, seed)
    directory = ctx.directory / scenario.cell / f"trial-{number:05d}"
    directory.mkdir(parents=True)
    reasons: list[str] = []
    report: EngineReport | None = None

    prefix = f"{ctx.batch}/{scenario.cell}/{number}"
    try:
        opened = await asyncio.to_thread(ctx.sink.open_trial, trial)
    except SinkError as exc:
        reasons.append(f"the sink did not open the trial: {exc}")
    else:
        if opened.get("rows") != 0:
            reasons.append(
                f"the sink's ledger for this trial already holds {opened.get('rows')} row(s); "
                "was the batch id reused?"
            )
    threads = sorted({f"{prefix}/{op.thread}" for op in scenario.ops})
    # Evidence only: recorded for the checker, never consulted by anything below.
    before = await asyncio.to_thread(
        evidence.read_before, ctx.sink, ctx.settings.direct_dsn, threads
    )
    try:
        existing = await asyncio.to_thread(rows_for_threads, ctx.settings.direct_dsn, threads)
    except Exception as exc:  # noqa: BLE001 - an unchecked start is not a fresh start
        reasons.append(f"could not check that the trial's threads are new: {exc}")
    else:
        if any(existing.values()):
            held = ", ".join(f"{table} {n}" for table, n in existing.items() if n)
            reasons.append(
                f"the target database already holds rows for this trial's threads ({held}); "
                "was the batch id reused?"
            )
    reasons.extend(await asyncio.to_thread(topology.unclean, ctx.toxiproxy, ctx.routes))

    history = HistoryWriter(directory / f"{ORCHESTRATOR}.jsonl", run=identity, proc=ORCHESTRATOR)
    socket_dir = socket_directory()
    links: list[ProcessWorker] = []

    def spawn(name: str, notes: asyncio.Queue[Note]) -> ProcessWorker:
        link = ProcessWorker(
            name=name,
            route=ctx.routes[name],
            run=identity,
            workload=scenario.workload,
            sink_url=ctx.settings.sink_url,
            trial_dir=directory,
            socket_dir=socket_dir,
            history=history,
            notes=notes,
            cwd=ctx.settings.root,
        )
        links.append(link)
        return link

    try:
        if not reasons:
            engine = TrialEngine(
                scenario,
                history=history,
                thread_prefix=prefix,
                spawn=spawn,
            )
            report = await engine.run()
    except Exception as exc:  # noqa: BLE001 - a harness failure makes the trial INVALID
        reasons.append(f"the orchestrator failed: {type(exc).__name__}: {exc}")
    finally:
        for link in links:
            await link.close()
        history.close()
        shutil.rmtree(socket_dir, ignore_errors=True)

    after = await _collect_sink(ctx, trial, identity, directory, reasons)
    reasons.extend(
        await asyncio.to_thread(
            evidence.write_after,
            directory,
            before,
            after,
            dsn=ctx.settings.direct_dsn,
            threads=threads,
            toxiproxy=ctx.toxiproxy,
            after_orchestrator_seq=history.last_seq,
            quiescent=all(link.reaped for link in links),
        )
    )
    sink_rows = len(after.rows) if after is not None else 0
    sink_pid = _sink_pid(after)
    result = assess(scenario, report, directory, reasons)
    _write_json(
        directory / "trial.json",
        _trial_record(ctx, identity, result, report, sink_rows, sink_pid),
    )
    return result


async def _collect_sink(
    ctx: _Batch, trial: TrialId, identity: RunIdentity, directory: Path, reasons: list[str]
) -> evidence.SinkAfter | None:
    """Write the sink's history (derived from its ledger) and check the sink is unchanged. The
    same read is the post-trial step's sink evidence; None when the sink could not be read."""
    try:
        health = await asyncio.to_thread(ctx.sink.health)
        rows = await asyncio.to_thread(ctx.sink.ledger, trial)
        chain = await asyncio.to_thread(ctx.sink.verify, trial)
    except SinkError as exc:
        reasons.append(f"the sink could not be read after the trial: {exc}")
        return None
    problems = check_sink(health, ctx.sink_source)
    reasons.extend(f"the sink after the trial: {p}" for p in problems)
    if not chain.get("ok"):
        reasons.append(f"the ledger's hash chain breaks at row {chain.get('first_bad')}")
    pid = health.get("pid")
    if rows:
        if not isinstance(pid, int):
            reasons.append("the sink does not report its pid; its history cannot be written")
        else:
            events = ledger_events(rows, identity, pid)
            text = "".join(event.to_line() for event in events)
            (directory / f"{SINK}.jsonl").write_bytes(text.encode("utf-8"))
    return evidence.SinkAfter(health=health, rows=rows, verify=chain)


def _sink_pid(after: evidence.SinkAfter | None) -> int | None:
    pid = after.health.get("pid") if after is not None else None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


# -- assessing a trial ------------------------------------------------------------------------


def _histories(directory: Path, reasons: list[str]) -> dict[str, tuple[tuple[Event, ...], bool]]:
    found: dict[str, tuple[tuple[Event, ...], bool]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        if path.name == "merged.jsonl":
            continue
        try:
            read = read_history(path)
        except HistoryError as exc:
            reasons.append(str(exc))
            continue
        found[path.stem] = (read.events, bool(read.partial_tail))
    return found


def _fired(events: Sequence[Event], step: FailpointStep) -> Event | None:
    arm = arm_of(step)
    for event in events:
        if (
            event.kind == "fp.hit"
            and event.status is None
            and event.fp == arm.point
            and (arm.op_id is None or event.op_id == arm.op_id)
            and event.data.get("occurrence") == arm.occurrence
            and event.data.get("action") == arm.action
        ):
            return event
    return None


def _ending_status(scenario: Scenario, worker: str, events: Sequence[Event]) -> int | None:
    """The status a failpoint step implies when its action ended the worker: the exit status
    the step declares, or minus the signal's number. None when no such action ran."""
    acted = {
        seq
        for e in events
        if e.kind == "fp.action" and isinstance(seq := e.data.get("hit_seq"), int)
    }
    for _, step in scenario.failpoint_steps():
        action = step.do
        if step.on.worker != worker or isinstance(action, Pause):
            continue
        hit = _fired(events, step)
        if hit is None or hit.seq not in acted:
            continue
        if isinstance(action, Exit):
            return action.exit
        return -int(getattr(signal, action.signal))
    return None


def _in_flight(worker: str, events: Sequence[Event]) -> list[str]:
    """The saver calls a worker ended by a step had started and not finished: the parked or
    ending call, and anything the same op ran beside it (LangGraph writes a step's pending writes
    beside its checkpoint). Whether those committed is the database's to say, not the worker's."""
    open_calls: Counter[tuple[str, str]] = Counter()
    for event in events:
        if not event.kind.startswith("saver."):
            continue
        method, _, phase = event.kind.removeprefix("saver.").rpartition(".")
        key = (method, str(event.op_id))
        open_calls[key] += 1 if phase == "call" else -1
    found = [f"{method} ({op})" for (method, op), n in sorted(open_calls.items()) for _ in range(n)]
    return [f"{worker} ended with saver calls in flight: {', '.join(found)}"] if found else []


def assess(
    scenario: Scenario, report: EngineReport | None, directory: Path, reasons: list[str]
) -> TrialResult:
    """Read back everything the trial wrote and say whether it can be trusted as evidence."""
    observations: list[str] = []
    histories = _histories(directory, reasons)
    events = {proc: evs for proc, (evs, _) in histories.items()}

    if report is not None:
        reasons.extend(f"protocol: {v}" for v in report.violations)
        reasons.extend(report.problems)
        reasons.extend(f"step {i} never fired" for i in report.unfired_steps)

    orchestrator = events.get(ORCHESTRATOR, ())
    if histories.get(ORCHESTRATOR, ((), False))[1]:
        reasons.append("the orchestrator's history ends in a partial line")

    # The client view, read back from the file: one invoke, then exactly one terminal event.
    invoked = Counter(e.op_id for e in orchestrator if e.kind == "client.invoke")
    ended = Counter(
        e.op_id for e in orchestrator if e.kind in ("client.ok", "client.fail", "client.info")
    )
    outcomes: Counter[str] = Counter(
        e.kind.removeprefix("client.")
        for e in orchestrator
        if e.kind in ("client.ok", "client.fail", "client.info")
    )
    for op, count in sorted(invoked.items(), key=lambda kv: str(kv[0])):
        if count != 1:
            reasons.append(f"op {op} was invoked {count} times")
        if ended[op] != 1:
            reasons.append(f"op {op} has {ended[op]} terminal events, not exactly one")
    for op in sorted((set(ended) - set(invoked)), key=str):
        reasons.append(f"op {op} has a terminal event but no invoke")

    if report is not None:
        for op_id, op in report.ops.items():
            if not op.sent:
                outcomes["not_sent"] += 1
                observations.append(f"op {op_id} {op.reason or 'was never sent'}")
            if op.late_result is not None:
                observations.append(f"op {op_id}: a {op.late_result} result came after its info")

    # Every scheduled failpoint fired where it was declared, and none failed to act.
    for proc, evs in events.items():
        for e in evs:
            if e.kind == "fp.hit" and e.status == "fail":
                reasons.append(f"{proc} seq {e.seq}: a failpoint could not act: {e.error}")
    for index, step in scenario.failpoint_steps():
        if _fired(events.get(step.on.worker, ()), step) is None:
            reasons.append(f"step {index} never fired ({step.on.fp} in {step.on.worker})")

    # Every worker started and exited, and its exit is explained.
    def procs(kind: str) -> set[str]:
        return {
            proc
            for e in orchestrator
            if e.kind == kind and isinstance(proc := e.data.get("proc"), str)
        }

    starts, exits = procs("worker.start"), procs("worker.exit")
    if report is not None:
        for name, worker in report.workers.items():
            if name not in starts:
                reasons.append(f"{name} has no worker.start")
            if name not in exits:
                reasons.append(f"{name} has no worker.exit")
                continue
            code = worker.returncode
            if code is None:
                continue
            worker_events = events.get(name, ())
            partial = histories.get(name, ((), False))[1]
            expected = _ending_status(scenario, name, worker_events)
            status = describe_status(code)
            shown = status["signal"] or f"status {code}"
            if worker.killed_by_step is not None:
                if code != -9:
                    reasons.append(f"{name}: killed by {worker.killed_by_step} but ended {shown}")
                observations.extend(_in_flight(name, worker_events))
            elif expected is not None:
                if code != expected:
                    reasons.append(f"{name}: its failpoint action implies {expected}, not {code}")
                observations.extend(_in_flight(name, worker_events))
            elif code in HARNESS_EXITS:
                reasons.append(f"{name} exited {code}: {HARNESS_EXITS[code]}")
            elif code == 0 and worker.shutdown_sent:
                if partial:
                    reasons.append(f"{name} shut down cleanly but its history ends mid-line")
            else:
                # A worker the schedule did not end (the OOM killer, a crash in the harness) broke
                # its contract: every run gets a result unless a scheduled fault ends the worker.
                reasons.append(f"{name} ended {shown}, which the schedule does not explain")

    if histories:
        try:
            merged = merge(evs for evs in events.values())
        except HistoryError as exc:
            reasons.append(f"the merge found a contradiction: {exc}")
        else:
            text = "".join(event.to_line() for event in merged)
            (directory / "merged.jsonl").write_bytes(text.encode("utf-8"))

    return TrialResult(
        number=_trial_number(directory),
        directory=directory,
        valid=not reasons,
        reasons=reasons,
        observations=observations,
        outcomes=outcomes,
    )


def _trial_number(directory: Path) -> int:
    return int(directory.name.removeprefix("trial-"))


def _trial_record(
    ctx: _Batch,
    identity: RunIdentity,
    result: TrialResult,
    report: EngineReport | None,
    sink_rows: int,
    sink_pid: int | None,
) -> dict[str, Any]:
    histories: dict[str, Any] = {}
    for path in sorted(result.directory.glob("*.jsonl")):
        raw = path.read_bytes()
        histories[path.stem] = {"lines": raw.count(b"\n"), "partial_tail": not raw.endswith(b"\n")}
    return {
        "v": 1,
        "batch": identity.batch,
        "cell": identity.cell,
        "trial": identity.trial,
        "seed": identity.seed,
        "valid": result.valid,
        "invalid_reasons": result.reasons,
        "observations": result.observations,
        "client_outcomes": dict(sorted(result.outcomes.items())),
        "ops": {k: vars(v) for k, v in report.ops.items()} if report else {},
        "workers": {
            name: {**vars(w), **(describe_status(w.returncode) if w.returncode is not None else {})}
            for name, w in (report.workers.items() if report else ())
        },
        "steps": {
            "barrier_fired": report.fired_steps if report else [],
            "barrier_unfired": report.unfired_steps if report else [],
        },
        "histories": histories,
        "sink": {"rows": sink_rows, "pid": sink_pid},
    }


# -- the batch --------------------------------------------------------------------------------


def run_batch(
    scenario: Scenario,
    options: RunOptions,
    settings: Settings,
    *,
    source: Path | None = None,
    progress: Callable[[TrialResult], None] | None = None,
    warn: Callable[[str], None] | None = None,
) -> BatchSummary:
    """Preflight, then every trial in order. RunRefused if the batch cannot start."""
    require_linux()
    if options.trials < 1:
        raise RunRefused("--trials must be at least 1")
    require_local_database(settings.direct_dsn, options.own_database)
    batch = _batch_id(options.batch)
    directory = options.out / batch
    if directory.exists():
        raise RunRefused(f"{directory} already exists; batch ids are never reused")
    # A pre-registered cell runs only through T-13's gate (crashcourt.orchestrator.preregistered).
    try:
        cell = preregistered.preregistered_cell(scenario, source, settings.root)
        authorization = (
            None
            if cell is None
            else preregistered.authorize(
                cell,
                source,
                options.trials,
                settings.root,
                canonical=options.canonical,
                instrument=options.instrument,
            )
        )
    except preregistered.GateRefused as exc:
        raise RunRefused(str(exc)) from exc

    probes = SystemProbes(settings.root)
    report = run_doctor(probes)
    manifest: Manifest | None = report.manifest
    if manifest is None:
        raise RunRefused("doctor did not pass:\n" + format_report(report))
    if authorization is not None:
        try:
            preregistered.confirm(authorization, manifest, settings.root)
        except preregistered.GateRefused as exc:
            raise RunRefused(str(exc)) from exc
    if options.canonical and manifest.git_dirty:
        raise RunRefused("canonical batch refused: the checkout has uncommitted changes")

    health = dict(probes.sink_health())
    source_hash = probes.sink_source_sha256()
    problems = check_sink(health, source_hash)
    if problems:
        raise RunRefused("the effect sink is not fit for a batch: " + "; ".join(problems))
    offset = health.get("clock_offset_ns")
    warning = check_clock(
        {
            "manifest": manifest.sink_clock_offset_ns,
            "batch start": offset if isinstance(offset, int) else None,
        },
        options.canonical,
    )
    if warning and warn is not None:
        warn(f"clock: {warning}")

    routes = topology.routes(scenario.workers, settings.direct_dsn, settings.worker_ports)
    toxiproxy = ToxiproxyClient(settings.toxiproxy_url)
    topology.ensure_routes(toxiproxy, routes)
    unclean = topology.unclean(toxiproxy, routes)
    if unclean:
        raise RunRefused("the worker routes are not plain paths: " + "; ".join(unclean))

    asyncio.run(_setup_tables(settings.direct_dsn))

    directory.mkdir(parents=True)
    if authorization is not None:
        preregistered.record(directory, authorization)
    manifest_bytes = manifest.to_json().encode("utf-8")
    (directory / "manifest.json").write_bytes(manifest_bytes)
    scenario_hash = scenario_sha256(scenario)
    record = BatchRecord(
        v=1,
        batch=batch,
        provenance="run",
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        scenario_sha256=scenario_hash,
    )
    (directory / "batch.json").write_bytes(
        (record.model_dump_json(indent=2) + "\n").encode("utf-8")
    )
    (directory / "scenario.json").write_bytes((canonical_json(scenario) + "\n").encode("utf-8"))
    if source is not None:
        shutil.copyfile(source, directory / "scenario.yaml")
    _write_json(
        directory / "run.json",
        {
            "v": 1,
            "batch": batch,
            "cell": scenario.cell,
            "mode": "canonical" if options.canonical else "ordinary",
            "preregistered_cell": authorization.cell if authorization else None,
            "phase": authorization.phase if authorization else None,
            "own_database": options.own_database,
            "started": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "trials": options.trials,
            "scenario": {
                "name": scenario.name,
                "sha256": scenario_hash,
                "file": _shown(source, settings.root) if source else None,
            },
            "master_seed": scenario.seed,
            "trial_seeds": {
                str(n): trial_seed(scenario.seed, scenario.cell, n)
                for n in range(1, options.trials + 1)
            },
            "manifest_sha256": record.manifest_sha256,
            "clause_registry_sha256": manifest.clause_registry_sha256,
            "clock": {
                "manifest_sink_offset_ns": manifest.sink_clock_offset_ns,
                "batch_start_sink_offset_ns": offset if isinstance(offset, int) else None,
                "limit_ns": CANONICAL_CLOCK_LIMIT_NS,
                "warning": warning,
            },
            "sink": {
                "url": settings.sink_url,
                "health": {k: v for k, v in health.items() if k != "mono_ns"},
                "expected_source_sha256": source_hash,
            },
            "routes": {name: route.describe() for name, route in routes.items()},
            # The direct path the post-trial snapshot reads (the oracle's), password removed.
            "oracle_dsn": redact(settings.direct_dsn),
            "pool": POOL_SETTINGS,
        },
    )

    ctx = _Batch(
        scenario=scenario,
        settings=settings,
        batch=batch,
        directory=directory,
        routes=routes,
        sink=SinkClient(settings.sink_url),
        toxiproxy=toxiproxy,
        sink_source=source_hash,
    )

    def unchanged() -> list[str]:
        if authorization is None:
            return []
        return preregistered.still_authorized(authorization, settings.root)

    results, revoked = asyncio.run(
        run_trials(lambda n: run_trial(ctx, n), options.trials, unchanged, progress)
    )
    if revoked:
        preregistered.revoke(directory, revoked)
    summary = BatchSummary(batch, directory, results, tuple(revoked))
    _write_json(directory / "summary.json", _summary_record(summary, scenario))
    return summary


async def run_trials(
    run_one: Callable[[int], Awaitable[TrialResult]],
    trials: int,
    unchanged: Callable[[], list[str]],
    progress: Callable[[TrialResult], None] | None = None,
) -> tuple[list[TrialResult], list[str]]:
    """Every trial in order. `unchanged` says why the checkout is no longer the authorized one;
    it is asked before each trial and after the last, and a reason stops the batch."""
    results: list[TrialResult] = []
    for number in range(1, trials + 1):
        if problems := unchanged():
            return results, problems
        result = await run_one(number)
        results.append(result)
        if progress is not None:
            progress(result)
    return results, unchanged()


def _summary_record(summary: BatchSummary, scenario: Scenario) -> dict[str, Any]:
    outcomes: Counter[str] = Counter()
    for trial in summary.trials:
        outcomes.update(trial.outcomes)
    return {
        "v": 1,
        "batch": summary.batch,
        "cell": scenario.cell,
        "trials": len(summary.trials),
        "valid": sum(t.valid for t in summary.trials),
        "invalid": sum(not t.valid for t in summary.trials),
        "client_outcomes": dict(sorted(outcomes.items())),
        "invalid_trials": {str(t.number): t.reasons for t in summary.trials if not t.valid},
        "observations": {str(t.number): t.observations for t in summary.trials if t.observations},
        **({"authorization_revoked": list(summary.revoked)} if summary.revoked else {}),
    }
