"""Scenarios: what one cell runs, from YAML validated into a frozen model with a content hash.

A scenario names its workload and target, the workers, the client operations, and a schedule of
trigger-action steps. Everything a trial does is fixed here; nothing is left to timing.

Operations. Each op is one client request, sent to one worker on one scenario thread. `after`
says when it is dispatched: `start` (as the trial starts) or the id of an earlier op (once that op
has its terminal client event, whichever it is). It is required, so equivalent YAML cannot state
the same order in two ways that hash differently.

Schedule. A step is a trigger and an action:

- `on: {fp: <point>, worker: <w>, op: <op>, occurrence: <n>}` fires in the worker, at the nth
  matching call to reach the failpoint (op may be left out to match any op of that worker). Its
  action runs there: `pause: <barrier>` parks the call until the orchestrator releases it;
  `signal: <name>` and `exit: <status>` end the worker at that point.
- `on: {all_paused: [<barrier>, ...]}` fires in the orchestrator, once, when every named barrier
  is held at the same time. Its action is `release: [<barrier>, ...]` (one barrier or several
  at once) or `kill: <worker>` (SIGKILL, recorded as a fault).

Loading refuses anything that could not run as declared, before any process starts: an unknown
failpoint, worker, op, or barrier; a failpoint step on a worker its op is not sent to; two steps
that could fire on one call; a barrier released twice; barriers that must be held together whose
ops are ordered one after the other; SIGSTOP at a failpoint, which nothing in a schedule can
continue yet.

The content hash (crashcourt.spec.content) is taken over the validated model, so it names the
scenario's meaning, not its formatting. `v` versions the format and enters the hash.

Scenario files read booleans as YAML 1.2 does: only true and false. YAML 1.1, which PyYAML
follows, also reads on, off, yes, and no as booleans, and would turn every step's `on:` key
into True.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    StringConstraints,
    Tag,
    ValidationError,
    model_validator,
)

from crashcourt.failpoints.registry import Arm, Point, Signal
from crashcourt.spec.content import NotCanonical, content_sha256
from crashcourt.spec.events import Version

#: A name that may appear in a path or a sink request: no separators, no leading dot.
PathName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
#: Workers are w1, w2, ...: the name is also the worker's proc in every history.
WorkerName = Annotated[str, StringConstraints(pattern=r"^w[1-9][0-9]*$")]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]

#: The most workers a scenario may use: no scenario needs a third yet.
MAX_WORKERS = 2
#: `after` value for an op dispatched as the trial starts.
START = "start"


class ScenarioError(ValueError):
    """The scenario cannot run as declared; nothing was started."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Target(_Frozen):
    """The system under test. V1's first target: LangGraph's async Postgres saver."""

    saver: Literal["postgres"]
    driver: Literal["async"]
    durability: Literal["sync", "async", "exit"]


class Op(_Frozen):
    id: PathName
    worker: WorkerName
    #: The scenario's name for the thread; each trial runs it as `<batch>/<cell>/<trial>/<thread>`.
    thread: PathName
    after: PathName
    input: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    #: A resume: the value the thread's pending `interrupt()` returns (`Command(resume=...)`).
    #: An op is a resume or carries input, never both.
    resume: JsonValue = None


class FailpointTrigger(_Frozen):
    fp: Point
    worker: WorkerName
    op: PathName | None = None
    occurrence: int = Field(default=1, ge=1, strict=True)


class AllPausedTrigger(_Frozen):
    all_paused: tuple[PathName, ...] = Field(min_length=1)


class Pause(_Frozen):
    pause: PathName


class SignalSelf(_Frozen):
    signal: Signal


class Exit(_Frozen):
    exit: int = Field(ge=0, le=255, strict=True)


class Release(_Frozen):
    release: tuple[PathName, ...] = Field(min_length=1)


class Kill(_Frozen):
    kill: WorkerName


def _key_of(*names: str) -> Any:
    def pick(value: object) -> str | None:
        data = value.model_dump() if isinstance(value, BaseModel) else value
        if isinstance(data, dict):
            for name in names:
                if name in data:
                    return name
        return None

    return Discriminator(pick)


FailpointAction = Annotated[
    Annotated[Pause, Tag("pause")]
    | Annotated[SignalSelf, Tag("signal")]
    | Annotated[Exit, Tag("exit")],
    _key_of("pause", "signal", "exit"),
]
BarrierAction = Annotated[
    Annotated[Release, Tag("release")] | Annotated[Kill, Tag("kill")],
    _key_of("release", "kill"),
]


class FailpointStep(_Frozen):
    on: FailpointTrigger
    do: FailpointAction


class BarrierStep(_Frozen):
    on: AllPausedTrigger
    do: BarrierAction


def _step_kind(value: object) -> str | None:
    data = value.model_dump() if isinstance(value, BaseModel) else value
    if not isinstance(data, dict):
        return None
    on: object = data.get("on")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if isinstance(on, dict):
        if "fp" in on:
            return "fp"
        if "all_paused" in on:
            return "all_paused"
    return None


Step = Annotated[
    Annotated[FailpointStep, Tag("fp")] | Annotated[BarrierStep, Tag("all_paused")],
    Discriminator(_step_kind),
]


class Timeouts(_Frozen):
    """Liveness bounds, never ordering: past one, the orchestrator stops waiting and says so."""

    #: From a run's dispatch to its result; past it the op ends as client.info.
    run_s: Seconds = 60.0
    #: From spawning a worker to its hello.
    hello_s: Seconds = 60.0
    #: From asking a worker to shut down (or losing its connection) to its exit.
    shutdown_s: Seconds = 30.0


class Scenario(_Frozen):
    v: Version
    name: PathName
    cell: PathName
    #: The batch's master seed; each trial's seed is derived from it (see `trial_seed`).
    seed: Annotated[str, StringConstraints(pattern=r"^\S+$")]
    workload: Literal["smoke", "w1", "w1-misuse", "w5"]
    target: Target
    workers: tuple[WorkerName, ...] = Field(min_length=1, max_length=MAX_WORKERS)
    ops: tuple[Op, ...] = Field(min_length=1)
    schedule: tuple[Step, ...] = ()
    timeouts: Timeouts = Timeouts()
    #: The landmark table the checker verifies this scenario's failpoint hits against, by name
    #: (scenarios/landmarks/<name>.yaml). Only the name's form is checked here; the checker
    #: resolves and verifies the table.
    landmarks: PathName | None = None

    @model_validator(mode="after")
    def _runs_as_declared(self) -> Self:
        problems = _problems(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def op(self, op_id: str) -> Op:
        return next(op for op in self.ops if op.id == op_id)

    def failpoint_steps(self) -> list[tuple[int, FailpointStep]]:
        return [(i, s) for i, s in enumerate(self.schedule) if isinstance(s, FailpointStep)]

    def barrier_steps(self) -> list[tuple[int, BarrierStep]]:
        return [(i, s) for i, s in enumerate(self.schedule) if isinstance(s, BarrierStep)]


def arm_of(step: FailpointStep) -> Arm:
    """The failpoint arm a step installs in its worker."""
    trigger, action = step.on, step.do
    common: dict[str, Any] = {
        "point": trigger.fp,
        "worker": trigger.worker,
        "op_id": trigger.op,
        "occurrence": trigger.occurrence,
    }
    if isinstance(action, Pause):
        return Arm(action="pause", **common)
    if isinstance(action, SignalSelf):
        return Arm(action="signal", signal=action.signal, **common)
    return Arm(action="exit", exit_code=action.exit, **common)


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for value in values:
        if value in seen and value not in repeated:
            repeated.append(value)
        seen.add(value)
    return repeated


def _ancestors(scenario: Scenario) -> dict[str, set[str]]:
    """Each op's ops that must end before it is dispatched (ops only name earlier ops)."""
    found: dict[str, set[str]] = {}
    for op in scenario.ops:
        found[op.id] = set() if op.after == START else {op.after, *found.get(op.after, set())}
    return found


def _problems(s: Scenario) -> list[str]:
    problems: list[str] = []
    for name in _duplicates(s.workers):
        problems.append(f"worker {name} is listed twice")
    workers = set(s.workers)

    ops: dict[str, Op] = {}
    for op in s.ops:
        if op.id == START:
            problems.append(f"op id {START!r} is reserved")
        if op.id in ops:
            problems.append(f"op {op.id} is declared twice")
        if op.worker not in workers:
            problems.append(f"op {op.id} is sent to {op.worker}, which is not a worker")
        if op.resume is not None and op.input:
            problems.append(f"op {op.id} is a resume and also carries input")
        if op.after != START and op.after not in ops:
            problems.append(
                f"op {op.id} runs after {op.after}, which is not an earlier op "
                f"(use {START!r} or the id of an op above it)"
            )
        ops[op.id] = op
    for worker in sorted(workers - {op.worker for op in s.ops}):
        problems.append(f"worker {worker} is sent no op")

    barriers: dict[str, FailpointStep] = {}
    arms: dict[tuple[str, str], list[FailpointStep]] = {}
    for index, step in s.failpoint_steps():
        where = f"step {index}"
        trigger = step.on
        if trigger.worker not in workers:
            problems.append(f"{where}: {trigger.worker} is not a worker")
        if trigger.op is not None:
            op = ops.get(trigger.op)
            if op is None:
                problems.append(f"{where}: op {trigger.op} is not declared")
            elif op.worker != trigger.worker:
                problems.append(
                    f"{where}: op {trigger.op} is sent to {op.worker}, so it never reaches a "
                    f"failpoint in {trigger.worker}"
                )
        if isinstance(step.do, SignalSelf) and step.do.signal == "SIGSTOP":
            problems.append(f"{where}: SIGSTOP at a failpoint needs a step that continues it")
        if isinstance(step.do, Pause):
            if step.do.pause in barriers:
                problems.append(f"{where}: barrier {step.do.pause} is declared twice")
            barriers[step.do.pause] = step
        try:
            arm_of(step)
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
        arms.setdefault((trigger.worker, trigger.fp), []).append(step)

    for (worker, point), steps in arms.items():
        # A failpoint step that matches any op must be the only one at its point, or one call
        # could fire two arms (or map a pause to the wrong barrier).
        if len(steps) > 1 and any(step.on.op is None for step in steps):
            problems.append(
                f"{worker} {point}: a step without an op must be the only step at that point"
            )
        keys = [f"{step.on.op} occurrence {step.on.occurrence}" for step in steps]
        for key in _duplicates(keys):
            problems.append(f"{worker} {point}: two steps fire on {key}")

    released: list[str] = []
    before = _ancestors(s) if not problems else {}
    for index, step in s.barrier_steps():
        where = f"step {index}"
        names = step.on.all_paused
        for name in _duplicates(names):
            problems.append(f"{where}: barrier {name} is listed twice")
        unknown = [name for name in names if name not in barriers]
        problems.extend(f"{where}: barrier {name} is not paused by any step" for name in unknown)
        held_ops = [barriers[n].on.op for n in names if n in barriers]
        for a in held_ops:
            for b in held_ops:
                if a is not None and b is not None and a in before.get(b, set()):
                    problems.append(
                        f"{where}: {b} runs after {a} ends, so their barriers are never held "
                        "together"
                    )
        action = step.do
        if isinstance(action, Release):
            for name in action.release:
                if name not in barriers:
                    problems.append(f"{where}: barrier {name} is not paused by any step")
                elif name not in names:
                    problems.append(f"{where}: releases {name}, which its trigger does not hold")
            released.extend(action.release)
        elif action.kill not in workers:
            problems.append(f"{where}: {action.kill} is not a worker")
    for name in _duplicates(released):
        problems.append(f"barrier {name} is released by two steps")
    if not problems:
        problems.extend(_order_independence(s, barriers, before))
    return problems


def _order_independence(
    s: Scenario, barriers: dict[str, FailpointStep], ancestors: dict[str, set[str]]
) -> list[str]:
    """Refuse schedules whose outcome would depend on which of two concurrent hits came first.

    - A barrier may be named by one trigger only: otherwise the first trigger to fire can
      release it before the other is complete, or release and kill it in either order.
    - A worker ended by a step (a kill, or a signal or exit at a failpoint) runs no other op
      when it ends: every other op sent to it is ordered before the ending call. Otherwise
      whether those ops ran, and how far, would be up to timing. Calls the same op makes
      concurrently (LangGraph writes a step's pending writes beside its checkpoint) can still
      be in flight; the trial records them, and pinning them takes a barrier of their own.
    - A worker is ended by one step at most: only one can act, so another would never fire.
    """
    problems: list[str] = []
    named: dict[str, list[int]] = {}
    for index, step in s.barrier_steps():
        for name in dict.fromkeys(step.on.all_paused):
            named.setdefault(name, []).append(index)
    for name, steps in named.items():
        if len(steps) > 1:
            listed = " and ".join(str(i) for i in steps)
            problems.append(
                f"barrier {name} is in the triggers of steps {listed}; which fires first would "
                "depend on the order of hits"
            )

    def only_before(where: str, worker: str, ending: set[str]) -> None:
        for op in s.ops:
            if (
                op.worker == worker
                and op.id not in ending
                and not any(op.id in ancestors.get(end, set()) for end in ending)
            ):
                problems.append(
                    f"{where}: op {op.id} on {worker} is not ordered before the call that "
                    f"ends {worker}"
                )

    kills: dict[str, list[int]] = {}
    for index, step in s.barrier_steps():
        if isinstance(step.do, Kill):
            worker = step.do.kill
            kills.setdefault(worker, []).append(index)
            parked = [barriers[n].on for n in step.on.all_paused if barriers[n].on.worker == worker]
            if not parked or any(on.op is None for on in parked):
                problems.append(
                    f"step {index}: kills {worker} without holding one of its calls by op, so "
                    f"what {worker} is doing then is up to timing"
                )
            else:
                only_before(f"step {index}", worker, {str(on.op) for on in parked})
    for index, step in s.failpoint_steps():
        if isinstance(step.do, Pause):
            continue
        kills.setdefault(step.on.worker, []).append(index)
        if step.on.op is None:
            problems.append(f"step {index}: a step that ends its worker must name its op")
        else:
            only_before(f"step {index}", step.on.worker, {step.on.op})
    for worker, steps in kills.items():
        if len(steps) > 1:
            listed = " and ".join(str(i) for i in sorted(steps))
            problems.append(f"{worker} is ended by steps {listed}; only one can act")
    return problems


class _Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans: `on`, `off`, `yes`, and `no` stay strings."""


_BOOL = "tag:yaml.org,2002:bool"
_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != _BOOL]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(  # pyright: ignore[reportUnknownMemberType]
    _BOOL, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def read_yaml(text: str) -> object:
    """Parse scenario YAML (YAML 1.2 booleans)."""
    return yaml.load(text, Loader=_Loader)  # a SafeLoader subclass


def load_scenario(path: Path) -> Scenario:
    """Read and validate a scenario file; ScenarioError says everything that is wrong with it."""
    try:
        data: object = read_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ScenarioError(f"{path}: cannot read: {exc}") from exc
    return parse_scenario(data, source=str(path))


def parse_scenario(data: object, *, source: str = "scenario") -> Scenario:
    try:
        scenario = Scenario.model_validate(data)
        content_sha256(scenario)  # refuse now what the batch record could not name
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or '(top)'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ScenarioError(f"{source}: {details}") from exc
    except NotCanonical as exc:
        raise ScenarioError(f"{source}: {exc}") from exc
    return scenario


def scenario_sha256(scenario: Scenario) -> str:
    """The content hash a batch record names its scenario by."""
    return content_sha256(scenario)


def trial_seed(master: str, cell: str, trial: int) -> str:
    """sha256 hex of the JSON array [master, cell, trial]: the seed of one trial.

    JSON keeps the three parts apart, so no two different triples encode the same bytes.
    Nothing randomizes yet; the seed is recorded on every event so later randomized cells can
    use it.
    """
    encoded = json.dumps([master, cell, trial], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
