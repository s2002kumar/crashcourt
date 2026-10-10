"""I2 (committed task writes are not re-executed) and I4 (every duplicate effect has a cause)."""

from __future__ import annotations

from collections import defaultdict
from itertools import pairwise

from crashcourt.checker.artifacts import LedgerRow
from crashcourt.checker.facts import Commit, TrialFacts, ledger_pointer, node_name, pointer
from crashcourt.checker.invariants._common import Context, thread_witnesses, trial_witnesses
from crashcourt.checker.observations import (
    BEFORE_INTERRUPT_IN_MODEL,
    COMMITTED_BEFORE_STOP,
    INTERRUPT_WITNESSED,
    RESUME_AFTER_INTERRUPT,
    RESUME_AFTER_STOP,
    STOP_WHILE_RUNNING,
    Anomaly,
    Observation,
    Witness,
    not_exercised,
    observed,
)
from crashcourt.checker.order import ref
from crashcourt.spec.events import Event

EXCEPTION = "exception"
CRASH = "crash"


def _case(facts: TrialFacts, task_id: str) -> str:
    """exception: a node failed in the same super-step (an ERROR write beside the task's writes);
    crash: any other interruption (a stop, a lost acknowledgment, a reset)."""
    return EXCEPTION if facts.error_write_beside(task_id) else CRASH


def _interrupted_commits(facts: TrialFacts, task_id: str) -> list[tuple[Commit, Event]]:
    """Commits of the task whose attempt was then interrupted, with what interrupted it."""
    found: list[tuple[Commit, Event]] = []
    for commit in facts.commits(task_id):
        interruption = facts.interruption(commit)
        if interruption is not None:
            found.append((commit, interruption))
    return found


def _committed_attempts(facts: TrialFacts, commit: Commit) -> tuple[str | None, dict[str, int]]:
    """The node of the committed run, and the effect attempts it posted, by effect key."""
    events = facts.order.by_proc[commit.worker][: commit.witness.seq]
    starts = [e for e in events if e.kind == "node.start" and e.task_id == commit.task_id]
    if not starts:
        return None, {}
    start = starts[-1]
    attempts: dict[str, int] = {}
    for e in events[start.seq :]:
        if e.kind == "effect.request" and e.effect_key is not None and e.attempt is not None:
            attempts[e.effect_key] = e.attempt
    return node_name(start), attempts


def i2(ctx: Context) -> Observation:
    """A task whose regular writes committed before its attempt was interrupted does not run
    again: no later node.start for the task, and no ledger attempt of its node above the one it
    committed with.

    Exercised: committed regular writes for a task (the call returned ok, the put_writes.after
    failpoint was hit, or a verified lost acknowledgment), then an interruption of that attempt (a
    stop of the worker, or the op ending in client.fail or client.info), then a later dispatch on
    the thread.
    """
    facts = ctx.facts
    anomalies: list[Anomaly] = []
    cases: set[str] = set()
    threads: set[str] = set()
    for task_id in sorted(facts.task_checkpoints):
        for commit, interruption in _interrupted_commits(facts, task_id):
            thread_id = commit.witness.thread_id or facts.thread_of(commit.witness)
            if thread_id is None or not any(
                facts.after(interruption, d) for d in facts.dispatches_on(thread_id)
            ):
                continue
            case = _case(facts, task_id)
            cases.add(case)
            threads.add(thread_id)
            # Any start of the task not before the commit is a re-run, including one no causal
            # path connects to the commit.
            reruns = [
                e
                for w in facts.workers
                for e in facts.order.by_proc[w]
                if e.kind == "node.start"
                and e.task_id == task_id
                and not (e.proc == commit.worker and e.seq < commit.witness.seq)
                and not facts.order.before(ref(e), ref(commit.witness))
            ]
            node, attempts = _committed_attempts(facts, commit)
            later_rows = [
                r
                for r in facts.ledger
                if r.node == node
                and r.effect_key in attempts
                and r.attempt > attempts[r.effect_key]
            ]
            if reruns or later_rows:
                anomalies.append(
                    Anomaly(
                        evidence=(
                            *commit.evidence,
                            pointer(interruption),
                            *(pointer(e) for e in reruns),
                            *(ledger_pointer(r) for r in later_rows),
                        ),
                        sub_reason=(
                            f"task {task_id} ran again after its writes committed ({case} case)"
                        ),
                        case=case,
                        witnesses=thread_witnesses(ctx, thread_id),
                    )
                )
    if not cases:
        return not_exercised(
            "I2",
            "no task with committed regular writes, an interrupted attempt, and a later dispatch",
        )
    return observed(
        "I2",
        anomalies,
        witnesses=trial_witnesses(ctx, threads),
        case=CRASH if CRASH in cases else EXCEPTION,
    )


def _cause_witnesses(ctx: Context, prev: LedgerRow, dup: LedgerRow) -> tuple[Witness, ...]:
    """The witnesses of I4's causes (a), (b), and (d) for one duplicate attempt."""
    facts = ctx.facts
    request = facts.request_for(prev)
    later = facts.request_for(dup)
    producing = facts.enclosing_node(request) if request is not None else None

    # (a) a stop while the producing node was running, then a dispatch of the later attempt's op.
    stop_evidence: tuple[str, ...] = ()
    resume_evidence: tuple[str, ...] = ()
    if request is not None and producing is not None:
        for stop in facts.stops_of(prev.worker):
            if stop.position is None or stop.position < request.seq:
                continue
            if all(run.start != producing.start for run in facts.running_at(stop)):
                continue
            dispatches = (
                [
                    d
                    for d in facts.ops[dup.op_id].dispatches
                    if d.data.get("to") == dup.worker and facts.after(stop.source, d)
                ]
                if dup.op_id in facts.ops
                else []
            )
            stop_evidence = (pointer(stop.source), pointer(producing.start))
            if dispatches:
                resume_evidence = (pointer(dispatches[0]),)
                break

    # (b) the model puts the effect before interrupt(), the task interrupted, and the later
    # attempt belongs to a resume op.
    spec = ctx.model.spec_for(prev.effect_key, prev.thread_id)
    interrupted = (
        facts.interrupt_evidence(producing.task_id)
        if producing is not None and producing.task_id is not None
        else ()
    )
    dup_op = ctx.scenario.op(dup.op_id)
    resumed = dup_op is not None and dup_op.kind == "resume" and dup.op_id != prev.op_id

    # (d) the producing task's regular writes committed before its attempt was interrupted.
    committed: tuple[str, ...] = ()
    if producing is not None and producing.task_id is not None:
        for commit, interruption in _interrupted_commits(facts, producing.task_id):
            if commit.worker == prev.worker and (
                request is None or commit.witness.seq > request.seq
            ):
                committed = (*commit.evidence, pointer(interruption))
                break

    return (
        Witness(name=STOP_WHILE_RUNNING, held=bool(stop_evidence), evidence=stop_evidence),
        Witness(name=RESUME_AFTER_STOP, held=bool(resume_evidence), evidence=resume_evidence),
        Witness(
            name=BEFORE_INTERRUPT_IN_MODEL,
            held=spec is not None and spec.before_interrupt,
        ),
        Witness(name=INTERRUPT_WITNESSED, held=bool(interrupted), evidence=interrupted),
        Witness(
            name=RESUME_AFTER_INTERRUPT,
            held=resumed,
            evidence=(pointer(later),) if resumed and later is not None else (),
        ),
        Witness(name=COMMITTED_BEFORE_STOP, held=bool(committed), evidence=committed),
    )


def i4(ctx: Context) -> Observation:
    """Every duplicate effect has a documented cause.

    Each attempt after the first of an effect key is one anomaly, carrying the witnesses of the
    causes in the specification's fixed order: (a) a stop while the producing node was running and
    a resume, on a sequential writer set; (b) an effect the model places before interrupt(), its
    task interrupted, and the duplicate from the resume; (c) concurrent writers or two resumes on
    the thread (the DISCLAIMED witness); (d) the producing task's regular writes committed before
    the stop. Layer B applies the order. Exercised: at least one ledger row.
    """
    facts = ctx.facts
    rows = [r for r in facts.ledger if r.thread_id in ctx.scenario.threads]
    if not rows:
        return not_exercised("I4", "no ledger row")
    by_key: dict[str, list[LedgerRow]] = defaultdict(list)
    for row in rows:
        by_key[row.effect_key].append(row)
    anomalies: list[Anomaly] = []
    for key in sorted(by_key):
        for prev, dup in pairwise(sorted(by_key[key], key=lambda r: r.sink_seq)):
            witnesses = _cause_witnesses(ctx, prev, dup)
            committed = next(w for w in witnesses if w.name == COMMITTED_BEFORE_STOP)
            request = facts.request_for(prev)
            producing = facts.enclosing_node(request) if request is not None else None
            case = (
                _case(facts, producing.task_id)
                if committed.held and producing is not None and producing.task_id is not None
                else None
            )
            anomalies.append(
                Anomaly(
                    evidence=(ledger_pointer(prev), ledger_pointer(dup)),
                    sub_reason=f"{key}: attempt {dup.attempt} after attempt {prev.attempt}",
                    case=case,
                    witnesses=(*thread_witnesses(ctx, dup.thread_id), *witnesses),
                )
            )
    # No duplicates: LG-PW's guarantee, unless a crash was witnessed on the thread (no verdict
    # cites LG-PW for a crash; decision 2 and the registry's note).
    windows = {
        _case(facts, task_id)
        for task_id in facts.task_checkpoints
        for commit, interruption in _interrupted_commits(facts, task_id)
        if any(
            facts.after(interruption, d)
            for d in facts.dispatches_on(commit.witness.thread_id or "")
        )
    }
    writers = {w for r in rows for w in facts.writers(r.thread_id)}
    crashed = CRASH in windows or any(s.worker in writers for s in facts.stops)
    case = CRASH if crashed else (EXCEPTION if windows else None)
    return observed(
        "I4",
        anomalies,
        witnesses=trial_witnesses(ctx, {r.thread_id for r in rows}),
        case=case,
    )
