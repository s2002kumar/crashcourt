"""I7 (no orphaned or phantom effects; approval matches effect), I8 (resume-value integrity), and
the I3 harness alarm.

I3 (client OK implies every effect on the path happened) is not a target invariant: its anomaly
means the harness's own records disagree, so it makes the trial INVALID and never produces an
outcome (founder decision 4).
"""

from __future__ import annotations

from crashcourt.checker.artifacts import LedgerRow
from crashcourt.checker.facts import data_text, ledger_pointer, pointer
from crashcourt.checker.invariants._common import Context, thread_witnesses, trial_witnesses
from crashcourt.checker.observations import (
    RESUME_AFTER_STOP,
    STOP_WHILE_RUNNING,
    WRITES_PENDING_AT_STOP,
    Anomaly,
    Observation,
    Witness,
    not_exercised,
    observed,
)

_I7_MISSING = "no ledger row and no executed entry"


def _rows(ctx: Context, thread_id: str) -> list[LedgerRow]:
    return [r for r in ctx.facts.ledger if r.thread_id == thread_id]


def _i7_threads(ctx: Context) -> list[str]:
    """Threads that exercise I7: at least one ledger row or one executed entry in head state."""
    return [t for t in ctx.scenario.threads if _rows(ctx, t) or ctx.states[t].head_executed]


def _rerun_witnesses(ctx: Context, row: LedgerRow) -> tuple[Witness, ...]:
    """For an orphan: a stop while its producing node was running, then a dispatch on the thread."""
    facts = ctx.facts
    request = facts.request_for(row)
    producing = facts.enclosing_node(request) if request is not None else None
    stop_evidence: tuple[str, ...] = ()
    resume_evidence: tuple[str, ...] = ()
    if request is not None and producing is not None:
        for stop in facts.stops_of(row.worker):
            if stop.position is None or stop.position < request.seq:
                continue
            if all(run.start != producing.start for run in facts.running_at(stop)):
                continue
            stop_evidence = (pointer(stop.source), pointer(producing.start))
            later = [d for d in facts.dispatches_on(row.thread_id) if facts.after(stop.source, d)]
            if later:
                resume_evidence = (pointer(later[0]),)
                break
    pending = facts.pending_at_stop(producing) if producing is not None else None
    return (
        Witness(name=STOP_WHILE_RUNNING, held=bool(stop_evidence), evidence=stop_evidence),
        Witness(name=RESUME_AFTER_STOP, held=bool(resume_evidence), evidence=resume_evidence),
        Witness(
            name=WRITES_PENDING_AT_STOP,
            held=pending is not None,
            evidence=(pointer(pending[0]), pointer(pending[1].source)) if pending else (),
        ),
    )


def i7a(ctx: Context) -> Observation:
    """Orphan: a ledger row (effect_key, attempt) not reflected in the head lineage, that is, in the
    executed channel of the head or of a checkpoint on its ancestor path. Effects the model places
    before interrupt() are judged by I4(b) only."""
    threads = _i7_threads(ctx)
    if not threads:
        return not_exercised("I7a", _I7_MISSING)
    anomalies: list[Anomaly] = []
    for thread_id in threads:
        reflected = ctx.states[thread_id].lineage_executed
        for row in _rows(ctx, thread_id):
            if ctx.model.judged_by_i4_only(row.effect_key, thread_id):
                continue
            if (row.effect_key, row.attempt) in reflected:
                continue
            anomalies.append(
                Anomaly(
                    evidence=(ledger_pointer(row),),
                    sub_reason=(
                        f"orphaned effect: {row.effect_key} attempt {row.attempt} is not in the "
                        "head lineage"
                    ),
                    witnesses=(*thread_witnesses(ctx, thread_id), *_rerun_witnesses(ctx, row)),
                )
            )
    return observed("I7a", anomalies, witnesses=trial_witnesses(ctx, set(threads)))


def i7b(ctx: Context) -> Observation:
    """Phantom: an executed entry in head state with no ledger row."""
    threads = _i7_threads(ctx)
    if not threads:
        return not_exercised("I7b", _I7_MISSING)
    anomalies: list[Anomaly] = []
    for thread_id in threads:
        state = ctx.states[thread_id]
        held = {(r.effect_key, r.attempt) for r in _rows(ctx, thread_id)}
        for key, attempt in state.head_executed:
            if (key, attempt) not in held:
                anomalies.append(
                    Anomaly(
                        evidence=(state.lineage_executed[(key, attempt)],),
                        sub_reason=f"phantom effect: head state records {key} attempt {attempt}",
                        witnesses=thread_witnesses(ctx, thread_id),
                    )
                )
    return observed("I7b", anomalies, witnesses=trial_witnesses(ctx, set(threads)))


def i7c(ctx: Context) -> Observation:
    """On W1, the approval effect has a ledger row exactly when the head's decision is approve."""
    template = ctx.model.approval_effect
    if template is None:
        return not_exercised("I7c", "the workload's effect model has no approval effect")
    threads = _i7_threads(ctx)
    if not threads:
        return not_exercised("I7c", _I7_MISSING)
    anomalies: list[Anomaly] = []
    for thread_id in threads:
        state = ctx.states[thread_id]
        key = template.format(thread=thread_id)
        rows = [r for r in _rows(ctx, thread_id) if r.effect_key == key]
        approved = state.head_decision == "approve"
        if bool(rows) != approved:
            anomalies.append(
                Anomaly(
                    evidence=(
                        *(ledger_pointer(r) for r in rows),
                        f"snapshot#checkpoint={thread_id}||{state.head}",
                    ),
                    sub_reason=(
                        f"approval mismatch: head decision {state.head_decision}, "
                        f"{len(rows)} {key} row(s)"
                    ),
                    witnesses=thread_witnesses(ctx, thread_id),
                )
            )
    return observed("I7c", anomalies, witnesses=trial_witnesses(ctx, set(threads)))


def i8(ctx: Context) -> Observation:
    """The decision each executing attempt acted on (data.decision on its effect.request) equals
    the RESUME value in the thread's head lineage.

    Exercised: an effect.request carrying data.decision. Without it I8 is omitted, never PASS.
    """
    facts = ctx.facts
    anomalies: list[Anomaly] = []
    threads: set[str] = set()
    for request in ctx.worker_events():
        acted = data_text(request, "decision")
        thread_id = facts.thread_of(request)
        if request.kind != "effect.request" or acted is None or thread_id is None:
            continue
        if thread_id not in ctx.states:
            continue
        threads.add(thread_id)
        resumes = ctx.states[thread_id].resume_decisions
        decisions = sorted({d for d, _ in resumes})
        problem: str | None = None
        if not decisions:
            problem = "no RESUME write in the head lineage"
        elif len(decisions) > 1:
            problem = f"the head lineage holds conflicting resume values {decisions}"
        elif decisions[0] != acted:
            problem = f"acted on {acted}; the head lineage resumed with {decisions[0]}"
        if problem is not None:
            anomalies.append(
                Anomaly(
                    evidence=(pointer(request), *(where for _, where in resumes)),
                    sub_reason=f"resume mismatch: {problem}",
                    witnesses=thread_witnesses(ctx, thread_id),
                )
            )
    if not threads:
        return not_exercised("I8", "no effect.request carrying data.decision")
    return observed("I8", anomalies, witnesses=trial_witnesses(ctx, threads))


def i3_alarm(ctx: Context) -> tuple[str, tuple[str, ...]] | None:
    """The I3 harness alarm (INVALID trigger 15): the head state claims an effect the ledger does
    not hold, or a client.ok run's effect-model path has a missing ledger row."""
    facts = ctx.facts
    for thread_id in ctx.scenario.threads:
        state = ctx.states[thread_id]
        held = {(r.effect_key, r.attempt) for r in _rows(ctx, thread_id)}
        for key, attempt in state.head_executed:
            if (key, attempt) not in held:
                return (
                    f"I3 harness alarm: head state claims {key} attempt {attempt}, which the "
                    "ledger does not hold",
                    (state.lineage_executed[(key, attempt)],),
                )
    keys = {r.effect_key for r in facts.ledger}
    for record in facts.ops.values():
        for ok in record.terminals:
            if ok.kind != "client.ok":
                continue
            missing = [k for k in ctx.model.expected_keys(record.spec) if k not in keys]
            if missing:
                return (
                    f"I3 harness alarm: op {record.spec.op_id} returned ok but the ledger holds "
                    f"no {', '.join(missing)}",
                    (pointer(ok),),
                )
    return None
