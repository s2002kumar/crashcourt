"""What the orchestrator declared for a trial, as the checker reads it (TrialScenario).

The checker judges a trial against the facts the batch declared before it ran, so this reads only
the batch's own records: scenario.json (the validated scenario), run.json, and manifest.json. One
function builds them for every caller, the runner's tests and T-13's verification alike.

A scenario does not name everything the checker needs, so a few fields follow fixed rules (T-10,
red-team specification 2.1 and 3.6), and anything the rules do not map to exactly one answer is
refused (NotDeclared) rather than guessed:

- An op's kind: in W1 (and its misuse variant) a resume is "resume" and anything else "start"; in
  W5 every op is a "turn", and a resume is refused.
- An arm's landmark: the one its step names (`landmark:`), which must be in the scenario's table
  (`landmarks: <name>`) at the arm's method and audited occurrence; anything else is refused. The
  scenario loader enforces the same rule, so a scenario that arms a failpoint always names both.
- Faults: a pause the scenario releases (when its barriers are held, or after an op ends) is a
  pause fault; a pause whose worker is killed while held is not (its hit is still checked), and the
  kill is a sigkill under the dispatcher's fault id; an exit arm is an exit fault; a SIGKILL arm is
  a sigkill fault. Any other signal is refused. An arm with action none is checked like any arm
  and is no fault. A redelivery is a redelivery fault naming its op and the worker it goes to.
- A whole-trial placebo toxic declares nothing: v1 has no fault kind for it, and an undeclared
  fault.inject would be INVALID, so the runner installs it without one, reads it back from
  Toxiproxy, and records it in trial.json (a trial without it is invalid).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from crashcourt.checker.artifacts import (
    ArmSpec,
    FaultSpec,
    LandmarkTable,
    OpKind,
    OpSpec,
    TrialScenario,
)
from crashcourt.checker.landmarks import LANDMARKS_ROOT, load_table
from crashcourt.checker.status import EvidenceClass
from crashcourt.manifest import Manifest
from crashcourt.orchestrator.dispatcher import kill_fault_id
from crashcourt.spec.scenario import (
    BarrierStep,
    EndedStep,
    Exit,
    FailpointStep,
    Inert,
    Kill,
    Pause,
    Redeliver,
    Release,
    Scenario,
    parse_scenario,
)

#: The saver class each target is, as clauses name it.
TARGETS: dict[tuple[str, str], str] = {
    ("postgres", "async"): "AsyncPostgresSaver",
    ("postgres", "sync"): "PostgresSaver",
}
#: The workloads the checker has effect models for.
WORKLOADS = ("w1", "w1-misuse", "w5")


class NotDeclared(ValueError):
    """The batch's records do not map to the checker's facts unambiguously."""


def trial_scenario(
    batch_dir: Path,
    trial: int,
    *,
    evidence: EvidenceClass,
    replication_of: str | None = None,
    matrix_cell: str | None = None,
    landmarks_root: Path = LANDMARKS_ROOT,
) -> TrialScenario:
    """The checker's facts for trial `trial` of the batch in `batch_dir`."""
    scenario = parse_scenario(_json(batch_dir / "scenario.json"), landmarks_root=landmarks_root)
    run: dict[str, Any] = _json(batch_dir / "run.json")
    raw = (batch_dir / "manifest.json").read_bytes()
    manifest = Manifest.from_json(raw.decode("utf-8"))

    batch = run.get("batch")
    if not isinstance(batch, str) or run.get("cell") != scenario.cell:
        raise NotDeclared(f"run.json names batch {batch!r} and cell {run.get('cell')!r}")
    cell = scenario.cell
    trials = run.get("trials")
    if not isinstance(trials, int) or not 1 <= trial <= trials:
        raise NotDeclared(f"trial {trial} is not one of the batch's {trials} trials")
    oracle_dsn = run.get("oracle_dsn")
    if not isinstance(oracle_dsn, str) or not oracle_dsn:
        raise NotDeclared("run.json does not record the direct DSN the snapshot reads")
    source = _field(run, "sink", "expected_source_sha256")
    if not isinstance(source, str):
        raise NotDeclared("run.json does not record the sink source the batch expected")
    proxies = _proxies(run)

    if scenario.workload not in WORKLOADS:
        raise NotDeclared(f"the checker has no effect model for workload {scenario.workload}")
    target = TARGETS.get((scenario.target.saver, scenario.target.driver))
    if target is None:
        raise NotDeclared(f"no saver class for target {scenario.target}")

    prefix = f"{batch}/{cell}/{trial}"
    ops = tuple(
        OpSpec(
            op_id=op.id,
            kind=_kind(scenario.workload, op.id, op.resume),
            thread_id=f"{prefix}/{op.thread}",
            logical_op=op.id,
            resume=_resume(op.id, op.resume),
        )
        for op in scenario.ops
    )
    table = _table(scenario, landmarks_root)
    arms, faults = _arms_and_faults(scenario, table)
    return TrialScenario(
        batch=batch,
        cell=cell,
        trial=trial,
        provenance="run",
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        evidence=evidence,
        replication_of=replication_of,
        matrix_cell=matrix_cell,
        workload=scenario.workload,
        durability=scenario.target.durability,
        target=target,
        threads=tuple(sorted({op.thread_id for op in ops})),
        ops=ops,
        arms=arms,
        faults=faults,
        landmarks=table,
        oracle_dsn=oracle_dsn,
        runner=manifest.runner,
        sink_source_sha256=source,
        proxies=proxies,
    )


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _field(record: dict[str, Any], *keys: str) -> object:
    value: object = record
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = cast("dict[str, object]", value).get(key)
    return value


def _proxies(run: dict[str, Any]) -> dict[str, str]:
    """Each worker's Toxiproxy proxy, from the routes the batch ran with."""
    routes = run.get("routes")
    if not isinstance(routes, dict) or not routes:
        raise NotDeclared("run.json does not record the worker routes")
    proxies: dict[str, str] = {}
    for worker, route in cast("dict[str, object]", routes).items():
        proxy = _field({"route": route}, "route", "proxy")
        if not isinstance(proxy, str):
            raise NotDeclared(f"run.json's route for {worker} names no proxy")
        proxies[proxy] = worker
    return proxies


def _kind(workload: str, op_id: str, resume: object) -> OpKind:
    if workload == "w5":
        if resume is not None:
            raise NotDeclared(f"op {op_id}: W5 sends turns, never a resume")
        return "turn"
    return "resume" if resume is not None else "start"


def _resume(op_id: str, resume: object) -> str | None:
    if resume is None or isinstance(resume, str):
        return resume
    raise NotDeclared(f"op {op_id}: the resume value {resume!r} is not a string")


def _table(scenario: Scenario, root: Path) -> LandmarkTable | None:
    if scenario.landmarks is None:
        return None
    path = root / f"{scenario.landmarks}.yaml"
    if not path.is_file():
        raise NotDeclared(f"landmark table {scenario.landmarks} is not in {root}")
    table = load_table(path)
    if table.name != scenario.landmarks:
        raise NotDeclared(f"{path.name} holds table {table.name}, not {scenario.landmarks}")
    return table


def _landmark(step: FailpointStep, table: LandmarkTable | None) -> str:
    """The landmark the step names, held to the table again: the loader already refused anything
    else, and this keeps the builder from trusting that."""
    method, _ = step.on.fp.split(".", 1)
    spec = next((s for s in table.landmarks if s.id == step.landmark), None) if table else None
    if spec is None or (spec.method, spec.occurrence) != (method, step.on.occurrence):
        where = f"table {table.name}" if table else "no landmark table"
        raise NotDeclared(
            f"{step.on.fp} occurrence {step.on.occurrence} names landmark {step.landmark}, "
            f"which {where} does not hold there"
        )
    return spec.id


def _arms_and_faults(
    scenario: Scenario, table: LandmarkTable | None
) -> tuple[tuple[ArmSpec, ...], tuple[FaultSpec, ...]]:
    released = {
        barrier
        for _, step in scenario.orchestrator_steps()
        if isinstance(step, BarrierStep | EndedStep) and isinstance(step.do, Release)
        for barrier in step.do.release
    }
    arms: list[ArmSpec] = []
    faults: list[FaultSpec] = []
    for index, step in enumerate(scenario.schedule):
        if isinstance(step, FailpointStep):
            arm = len(arms)
            common: dict[str, Any] = {
                "point": step.on.fp,
                "worker": step.on.worker,
                "op_id": step.on.op,
                "occurrence": step.on.occurrence,
                "landmark": _landmark(step, table),
            }
            action = step.do
            if isinstance(action, Pause):
                arms.append(ArmSpec(action="pause", **common))
                if action.pause in released:
                    faults.append(FaultSpec(kind="pause", arm=arm))
            elif isinstance(action, Exit):
                arms.append(ArmSpec(action="exit", exit_code=action.exit, **common))
                faults.append(FaultSpec(kind="exit", arm=arm, exit_code=action.exit))
            elif isinstance(action, Inert):
                arms.append(ArmSpec(action="none", **common))
            else:  # a signal the worker sends itself
                if action.signal != "SIGKILL":
                    raise NotDeclared(f"step {index}: no fault kind for a {action.signal} arm")
                arms.append(ArmSpec(action="signal", signal=action.signal, **common))
                faults.append(FaultSpec(kind="sigkill", arm=arm))
        elif isinstance(step.do, Kill):
            faults.append(
                FaultSpec(kind="sigkill", fault_id=kill_fault_id(index), worker=step.do.kill)
            )
        elif isinstance(step.do, Redeliver):
            redelivery = step.do.redeliver
            faults.append(
                FaultSpec(kind="redelivery", op_ids=(redelivery.op,), worker=redelivery.to)
            )
    return tuple(arms), tuple(faults)
