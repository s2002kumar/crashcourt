"""Trial-level INVALID triggers (red-team specification 1.2), checked before any predicate runs.

Each check returns the reason it failed, or None. The engine runs them in trigger order and the
first that fires makes every invariant of the trial INVALID, with that trigger named. Triggers 11
and 12 are crashcourt.checker.faults_fired; 14 (the checker raised) and 15 (the I3 harness
alarm) are the engine's.

Trigger 16 (founder's rule of 2026-10-10) covers a trial the harness did not control: the
orchestrator marked it invalid (a protocol violation, a run a timer ordered, an unexplained exit),
a worker ended in a way no declared fault explains, or an orchestrator fault struck a worker that
was not held at a declared barrier. What a controlled fault leaves behind is evaluated, not
invalidated: a saver call a killed worker had in flight may still commit, and the checker judges
the resulting rows (crashcourt.checker.integrity.check_snapshot).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence

from pydantic import BaseModel

from crashcourt.checker.artifacts import LedgerRow, Snapshot, TrialArtifacts
from crashcourt.checker.facts import TrialFacts, data_int, data_list, data_text, pointer
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
    1: "incomplete or mismatched trial",
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
    16: "uncontrolled trial",
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


def check_provenance(artifacts: TrialArtifacts) -> None:
    """Trigger 1: every record belongs to this trial, and its provenance is the evidence's."""
    scenario = artifacts.scenario
    fixture = scenario.evidence in {"synthetic", "artifact-mutant"}
    if fixture != (scenario.provenance == "fixture"):
        raise Invalid(
            1, f"{scenario.evidence} evidence with provenance {scenario.provenance} does not match"
        )
    identity = (scenario.batch, scenario.cell, scenario.trial)
    for history in artifacts.histories:
        for event in history.events:
            if (event.batch, event.cell, event.trial) != identity:
                raise Invalid(1, f"{pointer(event)} belongs to another trial")
    if artifacts.ledger is not None:
        for row in artifacts.ledger.rows:
            if row.thread_id not in scenario.threads:
                raise Invalid(1, f"ledger row {row.sink_seq} names thread {row.thread_id}")
    if scenario.provenance == "run":
        if artifacts.harness is None:
            raise Invalid(1, "a harness run needs the orchestrator's trial record")
        prefix = f"{scenario.batch}/{scenario.cell}/{scenario.trial}/"
        foreign = [t for t in scenario.threads if not t.startswith(prefix)]
        if foreign:
            raise Invalid(1, f"threads {foreign} are not scoped to this trial ({prefix})")


def check_harness(artifacts: TrialArtifacts) -> None:
    """Trigger 16: the orchestrator's own verdict on the trial."""
    if artifacts.harness is not None and not artifacts.harness.valid:
        reasons = "; ".join(artifacts.harness.invalid_reasons) or "no reason given"
        raise Invalid(16, f"the orchestrator marked the trial invalid: {reasons}")


def check_causal(by_proc: Mapping[str, Sequence[Event]], orchestrator: str) -> None:
    """Trigger 3: a worker acts only on ops dispatched to it, an fp.action names an earlier fp.hit
    of its process, and an effect.response follows its request."""
    dispatched = {
        (data_text(e, "to"), e.op_id)
        for e in by_proc.get(orchestrator, ())
        if e.kind.startswith("dispatch.")
    }
    for proc, events in by_proc.items():
        if proc == orchestrator or any(e.kind == "sink.append" for e in events):
            continue
        requested: set[tuple[str | None, str | None, int | None]] = set()
        for event in events:
            if event.op_id is not None and (proc, event.op_id) not in dispatched:
                raise Invalid(3, f"{pointer(event)} acts on op {event.op_id}, never sent to {proc}")
            if event.kind == "fp.action":
                hit_seq = data_int(event, "hit_seq")
                if (
                    hit_seq is None
                    or not 0 <= hit_seq < event.seq
                    or events[hit_seq].kind != "fp.hit"
                ):
                    raise Invalid(3, f"{pointer(event)} names no earlier fp.hit")
            if event.kind == "effect.request":
                requested.add((event.op_id, event.effect_key, event.attempt))
            if event.kind == "effect.response" and (
                (event.op_id, event.effect_key, event.attempt) not in requested
            ):
                raise Invalid(3, f"{pointer(event)} answers a request its process never made")


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


def check_snapshot(
    artifacts: TrialArtifacts, facts: TrialFacts
) -> tuple[Snapshot, tuple[str, ...]]:
    """Trigger 7: the snapshot was taken after quiescence, directly, twice, and the two agree.

    Returns the snapshot to evaluate and any notes. Two snapshots that differ only by rows that
    saver calls of a killed worker had in flight (a call with no return or raise in a worker that
    exited by a signal) are a controlled fault's consequence, not a broken trial: the second, later
    snapshot is evaluated and the difference is noted. Any other difference is INVALID.
    """
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
    if meta.dsn != artifacts.scenario.oracle_dsn or pair.second.meta.dsn != meta.dsn:
        raise Invalid(7, "the snapshot did not use the direct oracle DSN")
    if pair.first.same_rows(pair.second):
        return pair.first.canonical(), ()
    unexplained = _late_rows(pair.first, pair.second, _in_flight_calls(facts))
    if unexplained:
        raise Invalid(
            7, f"the two snapshots differ: something still wrote after quiescence ({unexplained})"
        )
    return pair.second.canonical(), (
        "rows written by a killed worker's in-flight saver call landed between the two "
        "snapshots; the second snapshot was evaluated",
    )


def _in_flight_calls(facts: TrialFacts) -> list[Event]:
    """put and put_writes calls with no return or raise, in workers that exited by a signal."""
    calls: list[Event] = []
    for worker in facts.workers:
        exit_ = facts.exits.get(worker)
        if exit_ is None or exit_.signal is None:
            continue
        events = facts.order.by_proc[worker]
        for call in events:
            if call.kind not in {"saver.put.call", "saver.put_writes.call"}:
                continue
            method = call.kind.split(".")[1]
            ended = any(
                e.kind in {f"saver.{method}.return", f"saver.{method}.raise"}
                and (e.thread_id, e.checkpoint_id, e.task_id)
                == (call.thread_id, call.checkpoint_id, call.task_id)
                for e in events[call.seq + 1 :]
            )
            if not ended:
                calls.append(call)
    return calls


def _late_rows(first: Snapshot, second: Snapshot, calls: Sequence[Event]) -> str | None:
    """None if the second snapshot only adds rows the in-flight calls wrote; else what differs."""
    puts = {
        (c.thread_id, c.checkpoint_ns or "", c.checkpoint_id)
        for c in calls
        if c.kind == "saver.put.call"
    }
    put_channels = {
        (c.thread_id, c.checkpoint_ns or "", channel)
        for c in calls
        if c.kind == "saver.put.call"
        for channel in data_list(c, "channels")
    }
    writes = {
        (c.thread_id, c.checkpoint_ns or "", c.checkpoint_id, c.task_id)
        for c in calls
        if c.kind == "saver.put_writes.call"
    }
    return (
        _diff(
            "checkpoints",
            {(r.thread_id, r.checkpoint_ns, r.checkpoint_id): r for r in first.checkpoints},
            {(r.thread_id, r.checkpoint_ns, r.checkpoint_id): r for r in second.checkpoints},
            lambda k: k in puts,
        )
        or _diff(
            "blobs",
            {(r.thread_id, r.checkpoint_ns, r.channel, r.version): r for r in first.blobs},
            {(r.thread_id, r.checkpoint_ns, r.channel, r.version): r for r in second.blobs},
            lambda k: k[:3] in put_channels,
        )
        or _diff(
            "writes",
            {
                (r.thread_id, r.checkpoint_ns, r.checkpoint_id, r.task_id, r.idx): r
                for r in first.writes
            },
            {
                (r.thread_id, r.checkpoint_ns, r.checkpoint_id, r.task_id, r.idx): r
                for r in second.writes
            },
            lambda k: k[:4] in writes,
        )
    )


def _diff(
    name: str,
    before: Mapping[tuple[object, ...], BaseModel],
    after: Mapping[tuple[object, ...], BaseModel],
    explained: Callable[[tuple[object, ...]], bool],
) -> str | None:
    if any(key not in after or after[key] != row for key, row in before.items()):
        return f"{name} rows changed or disappeared"
    if not all(explained(key) for key in after.keys() - before.keys()):
        return f"{name} rows that no in-flight call explains"
    return None


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
