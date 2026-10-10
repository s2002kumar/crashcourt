"""Fault-fired verification (red-team specification 3.6) and landmark verification (4.2).

A fault that did not fire, or fired somewhere other than declared, must never read as PASS: any
missing witness makes the trial INVALID, and the reason names it. Every arm must have produced
its fp.hit (trigger 10); every hit must sit at its declared landmark (trigger 11); every declared
fault must show its symptom in the targeted worker's history and in the oracle (trigger 12).

The landmark table is input data: the checker holds only the generic context rules a landmark is
made of, and no occurrence numbers. An arm with no landmark is accepted only in synthetic trials
and artifact mutants; any other trial with an unverifiable hit is INVALID.

Commit timestamps are compared with the inject and heal events' wall_ns only where 3.6 and
founder decision 7 require it (a lost acknowledgment's commit fell between them). No verdict
reads a monotonic clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from crashcourt.checker.artifacts import ArmSpec, FaultSpec, LandmarkTable
from crashcourt.checker.facts import TrialFacts, data_int, data_text, pointer
from crashcourt.checker.landmarks import matches, rule_holds
from crashcourt.checker.order import ref
from crashcourt.history.merge import Ref
from crashcourt.spec.events import Event

LOST_ACK_ERRORS = ("server closed the connection unexpectedly", "consuming input failed")
TERMINATED_ERRORS = ("57P01", "terminating connection due to administrator command")
CLOSED_ERRORS = (*TERMINATED_ERRORS, *LOST_ACK_ERRORS, "connection is closed", "connection is lost")
#: The toxic and stream each network fault row of 3.6 installs (T-05's toxics).
TOXICS: dict[str, tuple[str, str]] = {
    "lost-ack": ("timeout", "downstream"),
    "reset": ("reset_peer", "upstream"),
    "delayed-ack": ("latency", "downstream"),
}


@dataclass(frozen=True)
class FaultCheck:
    """The first failed check (trigger number and reason), or the witnesses of every fault."""

    trigger: int | None = None
    reason: str | None = None
    evidence: tuple[str, ...] = ()
    #: Saver calls whose commit a verified lost acknowledgment proves.
    committed_calls: frozenset[Ref] = field(default_factory=frozenset[Ref])
    notes: tuple[str, ...] = ()


class _Failed(Exception):
    def __init__(self, trigger: int, reason: str) -> None:
        super().__init__(reason)
        self.trigger = trigger
        self.reason = reason


def verify_faults(facts: TrialFacts, *, allow_unlandmarked: bool) -> FaultCheck:
    scenario = facts.scenario
    evidence: list[str] = []
    committed: set[Ref] = set()
    notes: list[str] = []
    try:
        hits = [_hit(facts, index, arm) for index, arm in enumerate(scenario.arms)]
        declared = {ref(h) for h in hits}
        for worker in facts.workers:
            for event in facts.order.by_proc[worker]:
                if event.kind == "fp.hit" and ref(event) not in declared:
                    raise _Failed(10, f"{pointer(event)} is a hit no arm of the scenario declares")
        for arm, hit in zip(scenario.arms, hits, strict=True):
            _check_landmark(facts, arm, hit, scenario.landmarks, allow_unlandmarked)
            evidence.append(pointer(hit))
        for fault in scenario.faults:
            hit = hits[fault.arm] if fault.arm is not None else None
            evidence.extend(_check_fault(facts, fault, hit, committed, notes))
    except _Failed as failed:
        return FaultCheck(trigger=failed.trigger, reason=failed.reason)
    return FaultCheck(
        evidence=tuple(dict.fromkeys(evidence)),
        committed_calls=frozenset(committed),
        notes=tuple(notes),
    )


# -- arms and landmarks -----------------------------------------------------------------------


def _hit(facts: TrialFacts, index: int, arm: ArmSpec) -> Event:
    events = facts.order.by_proc.get(arm.worker, ())
    hits = [
        e
        for e in events
        if e.kind == "fp.hit"
        and e.fp == arm.point
        and (arm.op_id is None or e.op_id == arm.op_id)
        and (arm.thread_id is None or e.thread_id == arm.thread_id)
        and (e.status == "fail" or data_int(e, "occurrence") == arm.occurrence)
    ]
    failed = [e for e in hits if e.status == "fail"]
    if failed:
        raise _Failed(10, f"arm {index} ({arm.point}) hit with status fail: {failed[0].error}")
    if len(hits) != 1:
        raise _Failed(
            10,
            f"arm {index} ({arm.point}, {arm.worker}, occurrence {arm.occurrence}) produced "
            f"{len(hits)} fp.hit events, not one",
        )
    hit = hits[0]
    if data_text(hit, "action") != arm.action:
        raise _Failed(
            10, f"arm {index} hit with action {data_text(hit, 'action')}, not {arm.action}"
        )
    return hit


def _call_of(events: Sequence[Event], hit: Event) -> Event | None:
    """The saver call a failpoint hit belongs to: the latest call of the same method, thread,
    checkpoint, and task before the hit."""
    method = str(hit.fp).split(".", 1)[0]
    for event in reversed(events[: hit.seq]):
        if (
            event.kind == f"saver.{method}.call"
            and event.thread_id == hit.thread_id
            and event.checkpoint_id == hit.checkpoint_id
            and event.task_id == hit.task_id
        ):
            return event
    return None


def _check_landmark(
    facts: TrialFacts,
    arm: ArmSpec,
    hit: Event,
    table: LandmarkTable | None,
    allow_unlandmarked: bool,
) -> None:
    if arm.landmark is None:
        if allow_unlandmarked:
            return
        raise _Failed(11, f"arm at {arm.point} names no landmark; its hit cannot be verified")
    spec = table.get(arm.landmark) if table is not None else None
    if table is None or spec is None:
        raise _Failed(11, f"landmark {arm.landmark} is not in the scenario's landmark table")
    if table.status != "audited" and not allow_unlandmarked:
        raise _Failed(11, f"landmark table {table.name} is a draft, not yet audited")
    if spec.method != arm.point.split(".", 1)[0]:
        raise _Failed(11, f"landmark {spec.id} is a {spec.method} call, the arm is at {arm.point}")
    events = facts.order.by_proc[hit.proc]
    call = _call_of(events, hit)
    if call is None or not matches(spec, call, hit.seq, events):
        failing = [
            r.rule for r in spec.rules if not rule_holds(r, hit.op_id, hit.seq, call, events)
        ]
        raise _Failed(
            11,
            f"hit {pointer(hit)} is not at landmark {spec.id}: {', '.join(failing) or 'no call'}",
        )


# -- faults ----------------------------------------------------------------------------------


def _action(facts: TrialFacts, hit: Event) -> Event:
    for event in facts.order.by_proc[hit.proc][hit.seq + 1 :]:
        if event.kind == "fp.action" and data_int(event, "hit_seq") == hit.seq:
            return event
    raise _Failed(12, f"no fp.action for hit {pointer(hit)}")


def _inject(facts: TrialFacts, fault: FaultSpec) -> tuple[Event, Event]:
    inject = next(
        (
            e
            for e in facts.orch_events
            if e.kind == "fault.inject"
            and fault.fault_id is not None
            and e.fault_id == fault.fault_id
        ),
        None,
    )
    if inject is None:
        raise _Failed(12, f"{fault.kind}: no fault.inject {fault.fault_id}")
    heal = facts.heal_of(inject)
    if heal is None:
        raise _Failed(9, f"{fault.kind}: fault.inject {fault.fault_id} has no fault.heal")
    return inject, heal


def _exit_signal(facts: TrialFacts, worker: str, signal: str) -> Event:
    exit_ = facts.exits.get(worker)
    if exit_ is None or exit_.signal != signal:
        raise _Failed(12, f"{worker} has no worker.exit with {signal}")
    return exit_.event


def _last_event(facts: TrialFacts, event: Event) -> None:
    if event.seq != len(facts.order.by_proc[event.proc]) - 1:
        raise _Failed(12, f"{event.proc} wrote events after {pointer(event)}")


def _call_outcome(facts: TrialFacts, hit: Event) -> tuple[Event, Event | None]:
    events = facts.order.by_proc[hit.proc]
    call = _call_of(events, hit)
    if call is None:
        raise _Failed(12, f"no saver call for hit {pointer(hit)}")
    method = call.kind.split(".")[1]
    for event in events[hit.seq + 1 :]:
        if (
            event.kind in {f"saver.{method}.return", f"saver.{method}.raise"}
            and event.thread_id == call.thread_id
            and event.checkpoint_id == call.checkpoint_id
            and event.task_id == call.task_id
        ):
            return call, event
    return call, None


def _rows_written(facts: TrialFacts, call: Event) -> list[int | None]:
    """Commit timestamps of the rows a put or put_writes call wrote, as the snapshot holds them."""
    ns = call.checkpoint_ns or ""
    if call.kind == "saver.put.call":
        return [
            r.commit_ns
            for r in facts.snapshot.checkpoints
            if (r.thread_id, r.checkpoint_ns, r.checkpoint_id)
            == (call.thread_id, ns, call.checkpoint_id)
        ]
    return [
        w.commit_ns
        for w in facts.snapshot.writes
        if (w.thread_id, w.checkpoint_ns, w.checkpoint_id, w.task_id)
        == (call.thread_id, ns, call.checkpoint_id, call.task_id)
    ]


def _need_hit(fault: FaultSpec, hit: Event | None) -> Event:
    if hit is None:
        raise _Failed(12, f"{fault.kind} names no arm")
    return hit


def _check_fault(
    facts: TrialFacts,
    fault: FaultSpec,
    hit: Event | None,
    committed: set[Ref],
    notes: list[str],
) -> list[str]:
    kind = fault.kind
    if kind == "pause":
        hit = _need_hit(fault, hit)
        release = next(
            (
                e
                for e in facts.orch_events
                if e.kind == "fp.release"
                and data_text(e, "proc") == hit.proc
                and data_int(e, "hit_seq") == hit.seq
            ),
            None,
        )
        if release is None:
            raise _Failed(12, f"pause: no fp.release for {pointer(hit)}")
        action = _action(facts, hit)
        if not facts.order.before(ref(release), ref(action)):
            raise _Failed(12, f"pause: {pointer(action)} does not follow its release")
        return [pointer(hit), pointer(release), pointer(action)]
    if kind in {"sigkill", "exit"}:
        if hit is None:
            inject, _ = _inject(facts, fault)
            worker = facts.fault_worker(inject) or str(fault.worker)
            return [pointer(inject), pointer(_exit_signal(facts, worker, "SIGKILL"))]
        action = _action(facts, hit)
        _last_event(facts, action)
        if kind == "sigkill":
            return [
                pointer(hit),
                pointer(action),
                pointer(_exit_signal(facts, hit.proc, "SIGKILL")),
            ]
        exit_ = facts.exits.get(hit.proc)
        expected = 1 if fault.exit_code is None else fault.exit_code
        if exit_ is None or exit_.returncode != expected or exit_.signal is not None:
            raise _Failed(12, f"exit: {hit.proc} has no worker.exit with status {expected}")
        return [pointer(hit), pointer(action), pointer(exit_.event)]
    if kind == "sigstop":
        # 3.6: the stop is at a failpoint (fp.hit, fp.action), with fault.inject SIGSTOP and
        # fault.heal SIGCONT recorded by the orchestrator.
        hit = _need_hit(fault, hit)
        action = _action(facts, hit)
        if data_text(action, "signal") != "SIGSTOP":
            raise _Failed(12, f"sigstop: {pointer(action)} did not send SIGSTOP")
        inject, heal = _inject(facts, fault)
        if facts.fault_worker(inject) not in (None, hit.proc):
            raise _Failed(12, f"sigstop: {fault.fault_id} names another worker than {hit.proc}")
        events = facts.order.by_proc[hit.proc]
        # The silence window: no event of the stopped worker ordered between the inject and the
        # heal. T-04's edges into a worker are dispatches and releases only, so this check finds a
        # breach only when one of those orders it; the specification's "silence window by seq" has
        # no stronger witness in schema v1 (recorded as an open reading).
        silent = [
            e
            for e in events
            if facts.order.before(ref(inject), ref(e)) and facts.order.before(ref(e), ref(heal))
        ]
        if silent:
            raise _Failed(12, f"sigstop: {hit.proc} wrote {pointer(silent[0])} while stopped")
        # Stopped at its fp.action, the worker could write nothing more until SIGCONT.
        if not any(e.seq > action.seq for e in events):
            raise _Failed(12, f"sigstop: {hit.proc} wrote nothing after SIGCONT")
        return [pointer(hit), pointer(action), pointer(inject), pointer(heal)]
    if kind in {"lost-ack", "reset", "delayed-ack"}:
        hit = _need_hit(fault, hit)
        if data_text(hit, "action") != "pause":
            raise _Failed(12, f"{kind}: the targeted call must be held at a pause arm")
        inject, heal = _inject(facts, fault)
        toxic, stream = TOXICS[kind]
        if (data_text(inject, "toxic"), data_text(inject, "stream")) != (toxic, stream):
            raise _Failed(
                12,
                f"{kind}: {fault.fault_id} installed {data_text(inject, 'toxic')} on the "
                f"{data_text(inject, 'stream')} stream, not {toxic} on the {stream} stream",
            )
        if facts.fault_worker(inject) != hit.proc:
            raise _Failed(12, f"{kind}: {fault.fault_id} is not on {hit.proc}'s proxy")
        release = next(
            (
                e
                for e in facts.orch_events
                if e.kind == "fp.release"
                and data_text(e, "proc") == hit.proc
                and data_int(e, "hit_seq") == hit.seq
            ),
            None,
        )
        if release is None or not inject.seq < release.seq < heal.seq:
            raise _Failed(
                12, f"{kind}: the toxic was not installed before the held call was released"
            )
        call, outcome = _call_outcome(facts, hit)
        rows = _rows_written(facts, call)
        found = [pointer(inject), pointer(release), pointer(heal), pointer(call)]
        if kind == "delayed-ack":
            if outcome is None or outcome.kind.endswith(".raise") or not rows:
                raise _Failed(12, f"delayed-ack: {pointer(call)} did not return ok with its row")
            notes.append(f"delayed-ack {fault.fault_id}: the return is not ordered after the heal")
            return [*found, pointer(outcome)]
        if (
            outcome is None
            or not outcome.kind.endswith(".raise")
            or not (outcome.error or "").startswith("OperationalError")
            or not any(p in (outcome.error or "") for p in LOST_ACK_ERRORS)
        ):
            raise _Failed(12, f"{kind}: {pointer(call)} did not raise a lost-connection error")
        if kind == "lost-ack":
            if not any(c is not None and inject.wall_ns < c < heal.wall_ns for c in rows):
                raise _Failed(
                    12,
                    f"lost-ack: the snapshot holds no row of {pointer(call)} committed "
                    "between the inject and the heal",
                )
            committed.add(ref(call))
        elif any(c is None or c <= heal.wall_ns for c in rows):
            # A later call (the retry) may write the same row; only one committed by the heal
            # can be this call's.
            raise _Failed(
                12, f"reset: the snapshot holds the row {pointer(call)} would have written"
            )
        return [*found, pointer(outcome)]
    if kind in {"terminate-backend", "db-restart"}:
        inject, heal = _inject(facts, fault)
        patterns = TERMINATED_ERRORS if kind == "terminate-backend" else CLOSED_ERRORS
        if kind == "terminate-backend" and (data_int(heal, "terminated") or 0) < 1:
            raise _Failed(12, "terminate-backend: the heal reports no terminated backend")
        if kind == "db-restart":
            if not all(isinstance(heal.data.get(k), int | float) for k in ("restart_s", "ready_s")):
                raise _Failed(12, "db-restart: the heal records no restart and ready durations")
            start = facts.orch_events[0].wall_ns if facts.orch_events else 0
            if facts.snapshot.meta.postmaster_start_ns <= start:
                raise _Failed(12, "db-restart: postmaster start time is not after the trial start")
        targets = [fault.worker] if fault.worker is not None else list(facts.workers)
        found = [pointer(inject), pointer(heal)]
        for worker in targets:
            raises = [
                e
                for e in facts.order.by_proc.get(str(worker), ())
                if e.kind.endswith(".raise")
                and e.kind.startswith("saver.")
                and not facts.order.before(ref(e), ref(inject))
                and any(p in (e.error or "") for p in patterns)
            ]
            if not raises:
                raise _Failed(12, f"{kind}: {worker} shows no connection error after the fault")
            found.append(pointer(raises[0]))
        return found
    if kind == "redelivery":
        op_id = fault.op_ids[0] if fault.op_ids else None
        record = facts.ops.get(str(op_id))
        redeliveries = [
            d
            for d in (record.dispatches if record is not None else ())
            if d.kind == "dispatch.redeliver" and data_text(d, "to") == fault.worker
        ]
        if record is None or not redeliveries:
            raise _Failed(12, f"redelivery: no dispatch.redeliver of {op_id} to {fault.worker}")
        reads = [
            e
            for e in facts.order.by_proc.get(str(fault.worker), ())
            if e.kind == "saver.get_tuple.call"
            and e.thread_id == record.spec.thread_id
            and e.op_id == op_id
            and facts.order.before(ref(redeliveries[0]), ref(e))
        ]
        if not reads:
            raise _Failed(12, f"redelivery: {fault.worker} never read the thread")
        return [pointer(redeliveries[0]), pointer(reads[0])]
    # second-resume
    if len(fault.op_ids) != 2:
        raise _Failed(12, "second-resume names two ops")
    first, second = (facts.ops.get(o) for o in fault.op_ids)
    if (
        first is None
        or second is None
        or first.spec.kind != "resume"
        or second.spec.kind != "resume"
        or first.spec.thread_id != second.spec.thread_id
        or not first.invokes
        or not second.invokes
        or not first.dispatches
        or not second.dispatches
    ):
        raise _Failed(12, "second-resume: two resume invokes on one thread are not both recorded")
    if not first.dispatches[0].seq < second.dispatches[0].seq:
        raise _Failed(12, "second-resume: the dispatches are not in the declared order")
    return [pointer(first.invokes[0]), pointer(second.invokes[0])]


# -- control (trigger 16) ------------------------------------------------------------------------


def verify_control(facts: TrialFacts, *, allow_uncontrolled: bool) -> FaultCheck:
    """Every worker that ended abnormally was ended by a declared fault, with the signal or status
    that fault implies; and every orchestrator fault struck a worker held at a declared barrier
    (artifact mutants, which plant stops on purpose, are exempt from the second rule)."""
    try:
        hits = {index: _hit(facts, index, arm) for index, arm in enumerate(facts.scenario.arms)}
        _check_exits(facts, hits)
        if not allow_uncontrolled:
            _check_controlled(facts, hits)
    except _Failed as failed:
        return FaultCheck(trigger=failed.trigger, reason=failed.reason)
    return FaultCheck()


def _check_exits(facts: TrialFacts, hits: dict[int, Event]) -> None:
    arm_by_hit = {ref(hit): facts.scenario.arms[index] for index, hit in hits.items()}
    for worker in sorted(facts.exits):
        exit_ = facts.exits[worker]
        if not exit_.abnormal:
            continue
        explained = False
        for action in (e for e in facts.order.by_proc.get(worker, ()) if e.kind == "fp.action"):
            arm = arm_by_hit.get(Ref(worker, data_int(action, "hit_seq") or -1))
            if arm is None:
                continue
            if arm.action == "exit":
                expected = 1 if arm.exit_code is None else arm.exit_code
                explained |= exit_.signal is None and exit_.returncode == expected
            elif arm.action == "signal" and arm.signal != "SIGSTOP":
                explained |= exit_.signal == arm.signal
        for fault in facts.scenario.faults:
            if fault.kind != "sigkill" or fault.fault_id is None:
                continue
            for inject in facts.orch_events:
                if inject.kind == "fault.inject" and inject.fault_id == fault.fault_id:
                    explained |= facts.fault_worker(inject) == worker and exit_.signal == "SIGKILL"
        if not explained:
            how = exit_.signal or f"status {exit_.returncode}"
            raise _Failed(16, f"{worker} ended with {how}, which no declared fault explains")


def _held(facts: TrialFacts, hits: dict[int, Event], worker: str | None) -> list[Event]:
    """Declared pause hits of a worker (any worker if None)."""
    return [
        hit
        for index, hit in hits.items()
        if facts.scenario.arms[index].action == "pause" and (worker is None or hit.proc == worker)
    ]


def _check_controlled(facts: TrialFacts, hits: dict[int, Event]) -> None:
    for fault in facts.scenario.faults:
        if fault.arm is not None or fault.fault_id is None:
            continue
        if fault.kind not in {"sigkill", "terminate-backend", "db-restart"}:
            continue
        inject = next(
            e
            for e in facts.orch_events
            if e.kind == "fault.inject" and e.fault_id == fault.fault_id
        )
        target = facts.fault_worker(inject) or fault.worker
        releases = {
            (data_text(e, "proc"), data_int(e, "hit_seq")): e
            for e in facts.orch_events
            if e.kind == "fp.release"
        }
        if fault.kind == "sigkill":
            # Killed while parked: its history ends at a declared pause hit nothing released.
            events = facts.order.by_proc.get(str(target), ())
            parked = [
                h
                for h in _held(facts, hits, target)
                if events and h.seq == events[-1].seq and (h.proc, h.seq) not in releases
            ]
        else:
            # Held across the fault: a declared pause hit whose release came after the inject.
            parked = [
                h
                for h in _held(facts, hits, None if fault.kind == "db-restart" else target)
                if (r := releases.get((h.proc, h.seq))) is not None and r.seq > inject.seq
            ]
        if not parked:
            raise _Failed(
                16, f"{fault.kind} {fault.fault_id} struck no worker held at a declared barrier"
            )
