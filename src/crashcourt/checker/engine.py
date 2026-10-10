"""classify(scenario, histories, ledger, snapshot, manifest, registry) -> result.json, checker.json.

A pure function of its inputs: no network, no database, no clock. The steps, in order:

1. Refuse the trial if a PRE-FREEZE checker may not see it (crashcourt.checker.status).
2. INVALID triggers 1-13 in order, decoding included (crashcourt.checker.integrity,
   crashcourt.checker.faults_fired), then the I3 harness alarm (trigger 15). The first that
   fires makes every invariant INVALID; a checker exception does too (trigger 14).
3. Layer A: each invariant's predicate observes ANOMALY, NO_ANOMALY, or NOT_EXERCISED.
4. Layer B: each exercised observation gets its outcome and clause. Unexercised invariants are
   omitted from result.json and listed in checker.json. If none was exercised there is no
   result.json (schema v1 requires at least one outcome), and checker.json says why.
"""

from __future__ import annotations

from dataclasses import dataclass

from crashcourt.checker.artifacts import Snapshot, TrialArtifacts
from crashcourt.checker.classify import Scope, classify
from crashcourt.checker.decode import DecodeError
from crashcourt.checker.effect_models import EFFECT_MODELS, ShapeError
from crashcourt.checker.facts import TrialFacts
from crashcourt.checker.faults_fired import FaultCheck, verify_control, verify_faults
from crashcourt.checker.freeze import FROZEN_PATH, landmark_hash, verify_trial
from crashcourt.checker.freeze import checker_sha256 as current_checker_sha256
from crashcourt.checker.integrity import (
    TRIGGERS,
    Invalid,
    check_causal,
    check_clients,
    check_complete,
    check_effects,
    check_environment,
    check_fault_events,
    check_harness,
    check_histories,
    check_ledger_links,
    check_node_runs,
    check_pretrial,
    check_provenance,
    check_sink,
    check_snapshot,
    check_worker_reports,
    roles,
)
from crashcourt.checker.invariants import INVARIANTS, PREDICATES, Context, i3_alarm
from crashcourt.checker.lineage import ChannelReader, build_lineages
from crashcourt.checker.mapping import RULES
from crashcourt.checker.order import Order
from crashcourt.checker.report import (
    CheckerReport,
    InvalidTrigger,
    InvariantDetail,
    NotExercised,
    to_json,
)
from crashcourt.checker.state import decode_states
from crashcourt.checker.status import CHECKER_VERSION, FREEZE_STATE, authorize
from crashcourt.clauses.registry import ClauseRegistry
from crashcourt.history.reader import HistoryError
from crashcourt.spec.content import content_sha256
from crashcourt.spec.records import SCHEMA_VERSION, InvariantResult, ResultRecord


@dataclass(frozen=True)
class CheckerOutput:
    result: ResultRecord | None
    report: CheckerReport

    def result_json(self) -> str | None:
        return to_json(self.result) if self.result is not None else None

    def report_json(self) -> str:
        return to_json(self.report)


def prepare(artifacts: TrialArtifacts) -> tuple[Context, FaultCheck]:
    """INVALID triggers 1-13 in order, then the context the predicates read. Raises Invalid."""
    scenario = artifacts.scenario
    model = EFFECT_MODELS[scenario.workload]
    orchestrator, sink = roles(artifacts)
    check_complete(artifacts, orchestrator, sink)
    check_provenance(artifacts)
    by_proc = check_histories(artifacts, orchestrator)
    try:
        order = Order(by_proc)
    except HistoryError as exc:
        raise Invalid(3, str(exc)) from exc
    assert artifacts.ledger is not None and artifacts.snapshots is not None
    check_ledger_links(by_proc, sink, artifacts.ledger.rows)
    check_node_runs(by_proc)
    check_causal(by_proc, orchestrator)
    check_clients(by_proc[orchestrator])

    def facts_with(judged: Snapshot, committed: FaultCheck | None) -> TrialFacts:
        assert artifacts.ledger is not None
        return TrialFacts(
            scenario,
            order,
            orchestrator=orchestrator,
            snapshot=judged,
            ledger=artifacts.ledger.rows,
            committed_calls=committed.committed_calls if committed is not None else (),
        )

    # The checks before the snapshot's own read no rows; they get the first snapshot in key
    # order. Everything after reads the snapshot trigger 7 says to evaluate.
    facts = facts_with(artifacts.snapshots.first.canonical(), None)
    check_effects(facts)
    check_sink(artifacts, facts)
    snapshot, snapshot_notes = check_snapshot(artifacts, facts)
    facts = facts_with(snapshot, None)
    lineages = build_lineages(snapshot)
    reader = ChannelReader(snapshot)
    try:
        states = decode_states(scenario.threads, lineages, reader, model)
    except (DecodeError, ShapeError) as exc:
        raise Invalid(7, f"a value the checker needs cannot be trusted: {exc}") from exc
    check_pretrial(artifacts)
    check_fault_events(facts)
    check_worker_reports(artifacts)
    faults = verify_faults(
        facts, allow_unlandmarked=scenario.evidence in {"synthetic", "artifact-mutant"}
    )
    if faults.trigger is not None:
        raise Invalid(faults.trigger, str(faults.reason))
    check_environment(artifacts)
    context = Context(
        scenario, facts_with(snapshot, faults), lineages, reader, model, states, snapshot_notes
    )
    return context, faults


def _observations(context: Context) -> tuple[str, ...]:
    """Records that are not outcomes: runs accepted and lost with no durable record, and the head
    each worker's latest reads observed."""
    facts = context.facts
    rows = {r.thread_id for r in facts.snapshot.checkpoints}
    found: list[str] = []
    for record in facts.ops.values():
        lost = [t for t in record.terminals if t.kind in {"client.info", "client.fail"}]
        if record.dispatches and lost and record.spec.thread_id not in rows:
            found.append(
                f"accepted run with no durable record: op {record.spec.op_id} ended "
                f"{lost[0].kind} and the snapshot holds no row for thread {record.spec.thread_id}"
            )
    for worker in facts.workers:
        for event in facts.order.by_proc[worker]:
            if (
                event.kind == "saver.get_tuple.return"
                and event.data.get("found") is True
                and event.data.get("requested") is None
            ):
                found.append(
                    f"{worker} seq {event.seq} observed head {event.checkpoint_id} on thread "
                    f"{event.thread_id} (op {event.op_id})"
                )
    return tuple(found)


def check_trial(
    artifacts: TrialArtifacts, registry: ClauseRegistry, *, registry_sha256: str
) -> CheckerOutput:
    scenario = artifacts.scenario
    authorize(scenario.evidence, scenario.replication_of, scenario.matrix_cell)
    frozen: str | None = None
    if FREEZE_STATE == "FROZEN":
        text = FROZEN_PATH.read_text(encoding="utf-8")
        frozen = verify_trial(text, registry_sha256, scenario.landmarks).checker_sha256
    report: dict[str, object] = {
        "checker_version": CHECKER_VERSION,
        "freeze_state": FREEZE_STATE,
        "authorized_for_new_evidence": FREEZE_STATE == "FROZEN",
        "frozen_sha256": frozen,
        "checker_sha256": current_checker_sha256(),
        "registry_sha256": registry_sha256,
        "effect_model_sha256": content_sha256(EFFECT_MODELS[scenario.workload]),
        "landmark_table_sha256": landmark_hash(scenario.landmarks) if scenario.landmarks else None,
        "batch": scenario.batch,
        "cell": scenario.cell,
        "trial": scenario.trial,
    }
    try:
        context, faults = prepare(artifacts)
        alarm = i3_alarm(context)
        if alarm is not None:
            raise Invalid(15, alarm[0])
        check_harness(artifacts)
        control = verify_control(
            context.facts, allow_uncontrolled=scenario.evidence == "artifact-mutant"
        )
        if control.trigger is not None:
            raise Invalid(control.trigger, str(control.reason))
        assert artifacts.manifest is not None
        scope = Scope(
            target=scenario.target,
            durability=scenario.durability,
            packages=artifacts.manifest.packages,
            fault_evidence=faults.evidence,
        )
        results: list[InvariantResult] = []
        missing: list[NotExercised] = []
        investigations: list[str] = []
        details: list[InvariantDetail] = []
        for name, predicate in PREDICATES:
            observation = predicate(context)
            classified = classify(observation, RULES[name], registry, scope)
            if classified is None:
                missing.append(NotExercised(invariant=name, missing=str(observation.missing)))
                continue
            results.append(classified.result)
            investigations.extend(classified.investigations)
            details.append(classified.detail)
        checker = CheckerReport.model_validate(
            {
                **report,
                "not_exercised": tuple(missing),
                "observations": (*context.notes, *_observations(context)),
                "harness_investigations": tuple(investigations),
                "fault_notes": faults.notes,
                "details": tuple(details),
            }
        )
    except Invalid as invalid:
        return _invalid(artifacts, report, invalid.trigger, invalid.reason)
    except Exception as exc:  # noqa: BLE001 - a checker that raised is trigger 14, not a verdict
        return _invalid(artifacts, report, 14, f"the checker raised {type(exc).__name__}: {exc}")
    record = (
        ResultRecord(
            v=SCHEMA_VERSION,
            batch=scenario.batch,
            cell=scenario.cell,
            trial=scenario.trial,
            provenance=scenario.provenance,
            manifest_sha256=scenario.manifest_sha256,
            results=tuple(results),
        )
        if results
        else None
    )
    return CheckerOutput(record, checker)


def _invalid(
    artifacts: TrialArtifacts, report: dict[str, object], trigger: int, reason: str
) -> CheckerOutput:
    scenario = artifacts.scenario
    text = f"trigger {trigger} ({TRIGGERS[trigger]}): {reason}"
    record = ResultRecord(
        v=SCHEMA_VERSION,
        batch=scenario.batch,
        cell=scenario.cell,
        trial=scenario.trial,
        provenance=scenario.provenance,
        manifest_sha256=scenario.manifest_sha256,
        results=tuple(
            InvariantResult(invariant=name, outcome="INVALID", reason=text) for name in INVARIANTS
        ),
    )
    checker = CheckerReport.model_validate(
        {
            **report,
            "invalid": InvalidTrigger(trigger=trigger, name=TRIGGERS[trigger], reason=reason),
        }
    )
    return CheckerOutput(record, checker)


__all__ = ["CheckerOutput", "check_trial", "prepare"]
