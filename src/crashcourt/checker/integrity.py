"""Trial-level INVALID triggers (red-team specification 1.2), checked before any predicate runs.

Each check returns the reason it failed, or None. The engine runs them in trigger order and the
first that fires makes every invariant of the trial INVALID, with that trigger named. Triggers 11
and 12 are crashcourt.checker.faults_fired; 14 (the checker raised) and 15 (the I3 harness
alarm) are the engine's.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from crashcourt.checker.artifacts import LedgerRow, Snapshot, TrialArtifacts
from crashcourt.checker.facts import TrialFacts, data_int, data_text, pointer
from crashcourt.history.merge import group_by_process
from crashcourt.history.reader import HistoryError
from crashcourt.spec.events import Event

REQUIRED_SETTINGS = {"fsync": "on", "synchronous_commit": "on", "track_commit_timestamp": "on"}
#: Errors of a request cut off by its worker's own death; any other failed response is INVALID.
CONNECTION_FAILURES = (
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "ConnectionRefusedError",
    "BrokenPipeError",
    "RemoteDisconnected",
)

#: The trigger numbers, with the short name each INVALID reason starts with.
TRIGGERS: dict[int, str] = {
    1: "incomplete trial",
    2: "unreadable history",
    3: "contradictory records",
    4: "client record",
    5: "effect without response",
    6: "sink",
    7: "snapshot",
    8: "pre-trial rows",
    9: "fault inject or heal",
    10: "failpoint",
    11: "landmark",
    12: "fault not fired",
    13: "environment",
    14: "checker",
    15: "I3 harness alarm",
}


class Invalid(Exception):
    def __init__(self, trigger: int, reason: str) -> None:
        super().__init__(f"trigger {trigger} ({TRIGGERS[trigger]}): {reason}")
        self.trigger = trigger
        self.reason = reason


def roles(artifacts: TrialArtifacts) -> tuple[str, str | None]:
    """The orchestrator's and the sink's process names (trigger 1 if not exactly one each)."""
    clients = sorted(
        {h.proc for h in artifacts.histories if any(e.kind.startswith("client.") for e in h.events)}
    )
    sinks = sorted(
        {h.proc for h in artifacts.histories if any(e.kind == "sink.append" for e in h.events)}
    )
    if len(clients) != 1:
        raise Invalid(1, f"expected one orchestrator history, found {clients or 'none'}")
    if len(sinks) > 1:
        raise Invalid(1, f"expected at most one sink history, found {sinks}")
    return clients[0], sinks[0] if sinks else None


def check_complete(artifacts: TrialArtifacts, orchestrator: str, sink: str | None) -> None:
    """Trigger 1: every record the trial must have, and every process its events name."""
    missing = [
        name
        for name, value in (
            ("manifest", artifacts.manifest),
            ("manifest hash", artifacts.manifest_sha256),
            ("ledger export", artifacts.ledger),
            ("snapshots", artifacts.snapshots),
        )
        if value is None
    ]
    if missing:
        raise Invalid(1, f"missing {', '.join(missing)}")
    procs = {h.proc for h in artifacts.histories}
    orch = next(h for h in artifacts.histories if h.proc == orchestrator).events
    named = {data_text(e, "to") for e in orch if e.kind.startswith("dispatch.")}
    named |= {data_text(e, "proc") for e in orch if e.kind in {"worker.start", "worker.exit"}}
    absent = sorted(str(p) for p in named - procs if p is not None)
    if absent:
        raise Invalid(1, f"no history for {', '.join(absent)}")
    invoked = {e.op_id for e in orch if e.kind == "client.invoke"}
    declared = {op.op_id for op in artifacts.scenario.ops}
    if invoked - declared:
        raise Invalid(
            1, f"ops {sorted(str(o) for o in invoked - declared)} are not in the scenario"
        )
    if declared - invoked:
        raise Invalid(1, f"scenario ops {sorted(declared - invoked)} were never invoked")
    assert artifacts.snapshots is not None
    uncovered = set(artifacts.scenario.threads) - set(artifacts.snapshots.first.meta.threads)
    if uncovered:
        raise Invalid(1, f"the snapshot does not cover threads {sorted(uncovered)}")
    workload_has_effects = bool(artifacts.ledger and artifacts.ledger.rows)
    if workload_has_effects and sink is None:
        raise Invalid(1, "the ledger holds rows but there is no sink history")
    if workload_has_effects and not any(h.when == "after" for h in artifacts.sink_health):
        raise Invalid(1, "no sink /health reading after the trial")
    for history in artifacts.histories:
        for event in history.events:
            if event.kind in {"node.start", "node.end"} and (
                event.task_id is None or data_text(event, "node") is None
            ):
                raise Invalid(1, f"{pointer(event)} has no task_id or data.node")


def check_histories(artifacts: TrialArtifacts, orchestrator: str) -> dict[str, tuple[Event, ...]]:
    """Trigger 2: every line parses, seq is contiguous, and only a process that exited with a
    signal may end in a partial line."""
    signalled: set[str] = set()
    orch = next(h for h in artifacts.histories if h.proc == orchestrator).events
    for event in orch:
        if event.kind == "worker.exit" and (
            data_text(event, "signal") is not None or (data_int(event, "returncode") or 0) < 0
        ):
            signalled.add(str(data_text(event, "proc")))
    for history in artifacts.histories:
        if history.parse_error is not None:
            raise Invalid(2, f"{history.proc}: {history.parse_error}")
        if history.partial_tail and history.proc not in signalled:
            raise Invalid(2, f"{history.proc} ends in a partial line but did not exit by a signal")
        if any(e.proc != history.proc for e in history.events):
            raise Invalid(2, f"{history.proc}'s history holds another process's events")
    try:
        return group_by_process(h.events for h in artifacts.histories)
    except HistoryError as exc:
        raise Invalid(2, str(exc)) from exc


def check_ledger_links(
    by_proc: Mapping[str, Sequence[Event]], sink: str | None, rows: Sequence[LedgerRow]
) -> None:
    """Trigger 3 (3.7's cross-check): each ledger row is one sink.append and one effect.request
    with the same effect key, op, attempt, worker, and pid; each append is a ledger row."""
    appends = {
        data_int(e, "sink_seq"): e for e in by_proc.get(sink or "", ()) if e.kind == "sink.append"
    }
    seqs = {r.sink_seq for r in rows}
    stray = sorted(s for s in appends if s is not None and s not in seqs)
    if stray:
        raise Invalid(3, f"sink.append rows {stray} are not in the ledger")
    for row in rows:
        append = appends.get(row.sink_seq)
        if append is None or (
            data_text(append, "worker"),
            append.op_id,
            append.effect_key,
            append.attempt,
        ) != (row.worker, row.op_id, row.effect_key, row.attempt):
            raise Invalid(3, f"ledger row {row.sink_seq} has no matching sink.append")
        requests = [
            e
            for e in by_proc.get(row.worker, ())
            if e.kind == "effect.request"
            and (e.op_id, e.effect_key, e.attempt, e.pid)
            == (row.op_id, row.effect_key, row.attempt, row.pid)
        ]
        if not requests:
            raise Invalid(3, f"ledger row {row.sink_seq} has no effect.request from {row.worker}")


def check_node_runs(by_proc: Mapping[str, Sequence[Event]]) -> None:
    """Trigger 3: every node.end closes a node.start of the same task in the same process. (A
    node that raised, an interrupt for one, may have no node.end.)"""
    for events in by_proc.values():
        open_tasks: set[str | None] = set()
        for event in events:
            if event.kind == "node.start":
                open_tasks.add(event.task_id)
            elif event.kind == "node.end":
                if event.task_id not in open_tasks:
                    raise Invalid(3, f"{pointer(event)} ends a node run that never started")
                open_tasks.discard(event.task_id)


def check_clients(orch: Sequence[Event]) -> None:
    """Trigger 4: one terminal event per invoke, and a result edge on every ok and fail."""
    for invoke in (e for e in orch if e.kind == "client.invoke"):
        terminals = [
            e
            for e in orch
            if e.op_id == invoke.op_id and e.kind in {"client.ok", "client.fail", "client.info"}
        ]
        if len(terminals) != 1:
            raise Invalid(4, f"op {invoke.op_id} has {len(terminals)} terminal events, not one")
        terminal = terminals[0]
        if terminal.kind != "client.info" and (
            data_text(terminal, "worker") is None or data_int(terminal, "worker_seq") is None
        ):
            raise Invalid(4, f"{pointer(terminal)} has no data.worker and data.worker_seq")


def check_effects(facts: TrialFacts) -> None:
    """Trigger 5: every effect.request has its response, unless the worker died."""
    for worker in facts.workers:
        events = facts.order.by_proc[worker]
        exit_ = facts.exits.get(worker)
        died = exit_ is not None and exit_.abnormal
        for request in (e for e in events if e.kind == "effect.request"):
            answered = any(
                e.kind == "effect.response"
                and (e.op_id, e.effect_key, e.attempt)
                == (request.op_id, request.effect_key, request.attempt)
                for e in events[request.seq + 1 :]
            )
            if not answered and not died:
                raise Invalid(5, f"{pointer(request)} has no effect.response")


def check_sink(artifacts: TrialArtifacts, facts: TrialFacts) -> None:
    """Trigger 6: the sink answered every request it was sent, its health readings are clean,
    and its ledger chain holds."""
    scenario = artifacts.scenario
    assert artifacts.ledger is not None
    for worker in facts.workers:
        exit_ = facts.exits.get(worker)
        for response in facts.order.by_proc[worker]:
            if response.kind != "effect.response" or response.status == "ok":
                continue
            died = exit_ is not None and exit_.abnormal
            cut_off = any(p in (response.error or "") for p in CONNECTION_FAILURES)
            if not (died and cut_off):
                raise Invalid(6, f"{pointer(response)} failed: {response.error}")
    trial = f"{scenario.batch}/{scenario.cell}/{scenario.trial}"
    if artifacts.sink_health and scenario.sink_source_sha256 is None:
        raise Invalid(6, "the scenario names no sink source hash to check /health against")
    for health in artifacts.sink_health:
        if health.hooks_enabled:
            raise Invalid(6, f"the sink reported test hooks enabled ({health.when})")
        if health.source_sha256 != scenario.sink_source_sha256:
            raise Invalid(6, f"the sink's source hash differs from the batch's ({health.when})")
        if health.open_trial is not None and health.open_trial != trial:
            raise Invalid(6, f"the sink had trial {health.open_trial} open, not {trial}")
    if not artifacts.ledger.verify_ok:
        raise Invalid(6, "the sink's /ledger/verify failed")
    rows = sorted(artifacts.ledger.rows, key=lambda r: r.sink_seq)
    previous = "0" * 64
    for expected, row in enumerate(rows, start=1):
        if row.sink_seq != expected or row.prev_hash != previous:
            raise Invalid(6, f"the ledger chain breaks at sink_seq {row.sink_seq}")
        previous = row.row_hash
    named = [
        data_int(e, "sink_seq") or 0
        for w in facts.workers
        for e in facts.order.by_proc[w]
        if e.kind == "effect.response" and e.status == "ok"
    ]
    if named and (not rows or rows[-1].sink_seq < max(named)):
        raise Invalid(6, "an effect.response names a ledger row past the ledger's end")


def check_snapshot(artifacts: TrialArtifacts, facts: TrialFacts) -> Snapshot:
    """Trigger 7: the snapshot was taken after quiescence, directly, twice, identically."""
    pair = artifacts.snapshots
    assert pair is not None
    meta = pair.first.meta
    # Every worker the orchestrator started or dispatched to, including one that logged nothing.
    named = {data_text(e, "to") for e in facts.orch_events if e.kind.startswith("dispatch.")}
    named |= {data_text(e, "proc") for e in facts.orch_events if e.kind == "worker.start"}
    for worker in sorted({*facts.workers, *(w for w in named if w is not None)}):
        exit_ = facts.exits.get(worker)
        if exit_ is None or exit_.event.seq > meta.after_orchestrator_seq:
            raise Invalid(7, f"the snapshot was taken before {worker} exited")
    # A heal after the snapshot is this trigger; a heal that never came is trigger 9's.
    for inject in (e for e in facts.orch_events if e.kind == "fault.inject"):
        heal = facts.heal_of(inject)
        if heal is not None and heal.seq > meta.after_orchestrator_seq:
            raise Invalid(7, f"the snapshot was taken before {inject.fault_id} was healed")
    if meta.toxics_installed or pair.second.meta.toxics_installed:
        raise Invalid(7, f"toxics were installed at snapshot time: {meta.toxics_installed}")
    if not pair.first.same_rows(pair.second):
        raise Invalid(7, "the two snapshots differ: something still wrote after quiescence")
    if meta.dsn != artifacts.scenario.oracle_dsn or pair.second.meta.dsn != meta.dsn:
        raise Invalid(7, "the snapshot did not use the direct oracle DSN")
    return pair.first


def check_pretrial(artifacts: TrialArtifacts) -> None:
    """Trigger 8: no rows for the trial's threads existed before the first dispatch."""
    assert artifacts.snapshots is not None
    counts = artifacts.snapshots.pretrial_rows
    for thread_id in artifacts.scenario.threads:
        if thread_id not in counts:
            raise Invalid(8, f"no pre-trial emptiness check for thread {thread_id}")
        if counts[thread_id]:
            raise Invalid(8, f"{counts[thread_id]} rows existed for {thread_id} before the trial")


def check_fault_events(facts: TrialFacts) -> None:
    """Trigger 9: every inject and heal succeeded, every inject has its heal, and every inject is
    a fault the scenario declares."""
    declared = {f.fault_id for f in facts.scenario.faults if f.fault_id is not None}
    for event in facts.orch_events:
        if event.kind == "fault.inject" and event.fault_id not in declared:
            raise Invalid(
                9, f"{pointer(event)} ({event.fault_id}) is not a fault the scenario declares"
            )
        if event.kind in {"fault.inject", "fault.heal"} and event.status == "fail":
            raise Invalid(9, f"{pointer(event)} failed: {event.error}")
        if event.kind == "fault.inject" and facts.heal_of(event) is None:
            raise Invalid(9, f"{pointer(event)} ({event.fault_id}) has no fault.heal")


def check_worker_reports(artifacts: TrialArtifacts) -> None:
    """Trigger 10 (in part): no worker's failpoint registry had a failed arm at shutdown."""
    for report in artifacts.worker_reports:
        if report.failpoints_failed:
            raise Invalid(10, f"{report.proc} had failed arms: {list(report.failpoints_failed)}")


def check_environment(artifacts: TrialArtifacts) -> None:
    """Trigger 13: the trial ran in the batch's environment, with the required settings."""
    manifest = artifacts.manifest
    assert manifest is not None and artifacts.snapshots is not None
    if hashlib.sha256(manifest.to_json().encode("utf-8")).hexdigest() != artifacts.manifest_sha256:
        raise Invalid(13, "the trial's manifest copy does not hash to the recorded sha256")
    if artifacts.manifest_sha256 != artifacts.scenario.manifest_sha256:
        raise Invalid(13, "the trial's manifest differs from the batch manifest")
    if manifest.runner != artifacts.scenario.runner:
        raise Invalid(
            13, f"runner {manifest.runner} differs from the batch's {artifacts.scenario.runner}"
        )
    for source, settings in (
        ("manifest", manifest.postgres),
        ("snapshot", artifacts.snapshots.first.meta.settings),
    ):
        for name, value in REQUIRED_SETTINGS.items():
            if settings.get(name) != value:
                raise Invalid(13, f"{source}: {name} is {settings.get(name)}, not {value}")
