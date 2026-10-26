"""What the N8 mitigations cost a fault-free run: `crashcourt mitigation bench`.

The performance measurement the pre-registration keeps out of the study's batches
(research/preregistration.md, "Performance"): baseline and protected runs interleaved after a
warm-up, reported as p50, p99, and throughput, with the raw distributions and the runner's
details. It measures cost. It is not an experiment: no fault, no checker, no prediction, and
nothing it writes is a batch or evidence about any cell.

The arms are the study's variants without the fault:

- base: no fence, a plain ledger;
- F: the fence in enforce mode. Every run holds an epoch, claims its thread, and binds the epoch on
  every connection it checks out, as a worker in a fenced batch does;
- I: an idempotent ledger (`apply_once`);
- FI: both.

A unit is one W1 run on a new thread: the start op, which ends at approval's interrupt, then the
resume with approve, which posts the charge. Each op goes through the worker's own `execute`, with
the worker's pool settings and the real AsyncPostgresSaver (no failpoint wrapper), under sync
durability, connected to Postgres directly rather than through Toxiproxy. The fenced arms use a
database with the fence installed and the unfenced arms a twin without it. Both are created on
the same server for the benchmark and dropped after it, so the arms interleave unit by unit and no
schema changes between them. Units never overlap: every saver call of one has returned before the
next starts.

The effects and attempt counters go to the sink's own `Ledger` class in this process, one ledger
file per client, rather than through the effect sink's HTTP service: the idempotent mode's cost is
the ledger's (one indexed lookup in the append's exclusive transaction), and the service's HTTP
round trips, its container's disk, and its one open trial at a time would otherwise be most of
what is timed and would serialize concurrent clients.

Each round has three phases:

- latency: a warm-up of `warmup` units per arm (kept, not summarized), then `units` per arm, one at
  a time, in a seeded shuffled order;
- throughput: one block per arm, in a seeded shuffled order, of `concurrency` clients that each run
  `per_client` units on their own threads with their own ledger; units per second over the
  block's wall time;
- ledger: `sink_posts` appends of each kind (plain; idempotent, a logical op's first request;
  idempotent, a duplicate of one), interleaved, with nothing else running.

Durations come from `time.perf_counter_ns`, and every one is kept in bench.json. The summary gives,
per round and arm, n, p50 and p99 (nearest rank), and the mean, and each arm's overhead against
base at p50 and p99. Three rounds give three values of each; no interval is computed from them.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import hashlib
import json
import math
import os
import platform
import random
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crashcourt.mitigation import fence

if TYPE_CHECKING:
    from crashcourt.sink.ledger import EffectRequest, Ledger, TrialId
    from crashcourt.spec.protocol import Result

ARMS = ("base", "F", "I", "FI")
FENCED = frozenset({"F", "FI"})
IDEMPOTENT = frozenset({"I", "FI"})
SINK_KINDS = ("plain", "once-first", "once-duplicate")
#: The databases the benchmark creates on the target server and drops when it ends.
DATABASES = {"plain": "crashcourt_bench_plain", "fenced": "crashcourt_bench_fenced"}
#: The saver and ledger calls a unit's record breaks out, in report order.
CALLS = ("claim", "aget_tuple", "aput", "aput_writes", "ledger.next_attempt", "ledger.append")
BENCH_VERSION = 1
BENCH_FILE = "bench.json"
REPORT_FILE = "bench.md"


@dataclass(frozen=True)
class BenchOptions:
    out: Path
    rounds: int = 3
    units: int = 300
    warmup: int = 30
    concurrency: int = 8
    per_client: int = 25
    sink_posts: int = 300
    seed: int = 1

    def problems(self) -> list[str]:
        sizes = {
            "rounds": self.rounds,
            "units": self.units,
            "concurrency": self.concurrency,
            "per_client": self.per_client,
            "sink_posts": self.sink_posts,
        }
        found = [f"{name} must be at least 1" for name, value in sizes.items() if value < 1]
        if self.warmup < 0:
            found.append("warmup must not be negative")
        return found


class BenchError(Exception):
    """The benchmark cannot run, or a unit did not end as a fault-free W1 run ends."""


def percentile(samples: Sequence[int], q: float) -> int:
    """The nearest-rank percentile: the smallest sample with at least a share q of the samples
    at or below it."""
    if not samples:
        raise ValueError("no samples")
    if not 0 < q <= 1:
        raise ValueError(f"q must be in (0, 1], not {q}")
    ordered = sorted(samples)
    return ordered[math.ceil(q * len(ordered)) - 1]


def describe(samples: Sequence[int]) -> dict[str, int]:
    """n, p50, p99, mean, min, and max of durations in nanoseconds."""
    return {
        "n": len(samples),
        "p50_ns": percentile(samples, 0.5),
        "p99_ns": percentile(samples, 0.99),
        "mean_ns": round(sum(samples) / len(samples)),
        "min_ns": min(samples),
        "max_ns": max(samples),
    }


def schedule(kinds: Sequence[str], per_kind: int, rng: random.Random) -> list[str]:
    """`per_kind` of every kind, in one shuffled order."""
    order = [kind for kind in kinds for _ in range(per_kind)]
    rng.shuffle(order)
    return order


# ---------------------------------------------------------------------------------------------
# Timing: every saver and ledger call a unit makes, by name, and the saver calls still running.


class UnitCalls:
    """The calls one unit made, with their durations. Saver calls are counted while they run, so
    the unit can wait for every one of them before the next unit starts."""

    def __init__(self) -> None:
        self.durations: dict[str, list[int]] = {}
        self._running = 0
        self._idle = asyncio.Event()
        self._idle.set()
        #: Saver calls still running when the unit's last op returned.
        self.late = 0

    def add(self, name: str, ns: int) -> None:
        self.durations.setdefault(name, []).append(ns)

    def enter(self) -> None:
        self._running += 1
        self._idle.clear()

    def leave(self, name: str, ns: int) -> None:
        self.add(name, ns)
        self._running -= 1
        if self._running == 0:
            self._idle.set()

    async def settle(self) -> None:
        self.late = self._running
        await self._idle.wait()


_unit: contextvars.ContextVar[UnitCalls | None] = contextvars.ContextVar(
    "crashcourt_bench_unit", default=None
)


async def _timed(name: str, call: Awaitable[Any]) -> Any:
    calls = _unit.get()
    if calls is None:
        return await call
    calls.enter()
    started = time.perf_counter_ns()
    try:
        return await call
    finally:
        calls.leave(name, time.perf_counter_ns() - started)


def _add(name: str, ns: int) -> None:
    calls = _unit.get()
    if calls is not None:
        calls.add(name, ns)


def timed_saver_class() -> type[Any]:
    """AsyncPostgresSaver with its three calls in a fault-free W1 run timed; nothing else
    changes."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    class TimedSaver(AsyncPostgresSaver):
        async def aget_tuple(self, *args: Any, **kwargs: Any) -> Any:
            return await _timed("aget_tuple", super().aget_tuple(*args, **kwargs))

        async def aput(self, *args: Any, **kwargs: Any) -> Any:
            return await _timed("aput", super().aput(*args, **kwargs))

        async def aput_writes(self, *args: Any, **kwargs: Any) -> Any:
            return await _timed("aput_writes", super().aput_writes(*args, **kwargs))

    return TimedSaver


class LedgerSink:
    """The sink's two calls, straight to one ledger in this process, timed. W1's nodes are
    synchronous, so these run in LangGraph's executor threads, under the unit's context."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def post_effect(self, trial: TrialId, effect: EffectRequest) -> dict[str, Any]:
        if trial != self.ledger.trial:
            raise BenchError(f"an effect for {trial} reached the ledger of {self.ledger.trial}")
        started = time.perf_counter_ns()
        answer = self.ledger.append(effect)
        _add("ledger.append", time.perf_counter_ns() - started)
        return answer

    def next_attempt(self, trial: TrialId, key: str) -> int:
        if trial != self.ledger.trial:
            raise BenchError(f"a counter for {trial} reached the ledger of {self.ledger.trial}")
        started = time.perf_counter_ns()
        value = self.ledger.next_attempt(key)
        _add("ledger.next_attempt", time.perf_counter_ns() - started)
        return value


# ---------------------------------------------------------------------------------------------
# One arm: its pool and saver, and, per client of a phase, a W1 graph with its own ledger.


@dataclass
class Client:
    graph: Any
    history: Any
    ledger: Ledger
    units: int = 0


@dataclass
class Arm:
    name: str
    pool: Any
    saver: Any
    scratch: Path
    batch: str
    ledgers: list[tuple[Ledger, int]] = field(default_factory=list[tuple["Ledger", int]])

    @property
    def fenced(self) -> bool:
        return self.name in FENCED

    @property
    def idempotent(self) -> bool:
        return self.name in IDEMPOTENT

    def client(self, phase: str, round_: int, number: int) -> Client:
        """A W1 graph whose effects and counters go to a new ledger of its own."""
        from crashcourt.history.writer import RunIdentity
        from crashcourt.sink.ledger import Ledger, TrialId
        from crashcourt.worker.main import WorkerHistory
        from crashcourt.workloads import w1_approval
        from crashcourt.workloads.harness import Harness

        name = f"{phase}-{self.name}-{round_}-c{number}"
        trial = TrialId(batch=self.batch, cell=name, trial=round_, seed=f"bench-{name}")
        ledger = Ledger(self.scratch / f"{name}.sqlite", trial, apply_once=self.idempotent)
        history = WorkerHistory(
            self.scratch / f"{name}.jsonl",
            run=RunIdentity(batch=trial.batch, cell=trial.cell, trial=trial.trial, seed=trial.seed),
            proc="bench",
        )
        graph = w1_approval.build(self.saver, Harness(history, LedgerSink(ledger)))
        return Client(graph, history, ledger)

    def finish(self, client: Client) -> None:
        """Close the client's history; its ledger is checked at the end of the benchmark."""
        client.history.close()
        self.ledgers.append((client.ledger, client.units))

    async def claim(self, run: Any) -> None:
        started = time.perf_counter_ns()
        async with self.pool.connection() as conn:
            await fence.claim(conn, run.thread_id, run.epoch)
        _add("claim", time.perf_counter_ns() - started)


def _check(op: str, thread: str, result: Result) -> None:
    from crashcourt.workloads.w1_approval import APPROVE

    output = result.output if isinstance(result.output, dict) else {}
    if result.status != "ok":
        raise BenchError(f"{thread} {op}: {result.status}: {result.error}")
    if op == "start" and output.get("interrupted") is not True:
        raise BenchError(f"{thread} start did not end at approval's interrupt: {output}")
    executed = output.get("executed")
    if op == "resume" and (
        output.get("decision") != APPROVE or not isinstance(executed, list) or len(executed) != 1
    ):
        raise BenchError(f"{thread} resume did not record one charge for approve: {output}")


async def run_unit(arm: Arm, client: Client, thread: str, n: int) -> dict[str, Any]:
    """One W1 run, start then resume, in its own context: the fence's epoch and the unit's
    timings never leak into another unit."""
    from crashcourt.spec.protocol import Run
    from crashcourt.worker.main import execute
    from crashcourt.workloads.w1_approval import APPROVE, summarize

    async def unit() -> dict[str, Any]:
        calls = UnitCalls()
        _unit.set(calls)
        record: dict[str, Any] = {"thread": thread}
        runs = (
            ("start", Run(op_id=f"start-{n}", thread_id=thread, durability="sync",
                          input={"order": f"order-{n}"}, epoch=1 if arm.fenced else None)),
            ("resume", Run(op_id=f"resume-{n}", thread_id=thread, durability="sync",
                           resume=APPROVE, epoch=2 if arm.fenced else None)),
        )  # fmt: skip
        for op, run in runs:
            claim = arm.claim if arm.fenced else None
            started = time.perf_counter_ns()
            result = await execute(client.graph, summarize, client.history, run, claim=claim)
            record[f"{op}_ns"] = time.perf_counter_ns() - started
            _check(op, thread, result)
        await calls.settle()
        record["unit_ns"] = record["start_ns"] + record["resume_ns"]
        record["late"] = calls.late
        record["calls"] = calls.durations
        return record

    record = await asyncio.create_task(unit())
    client.units += 1
    return record


# ---------------------------------------------------------------------------------------------
# The phases.


async def latency_phase(
    arms: Mapping[str, Arm], round_: int, options: BenchOptions, rng: random.Random, prefix: str
) -> dict[str, Any]:
    clients = {name: arm.client("latency", round_, 0) for name, arm in arms.items()}
    order = schedule(ARMS, options.warmup, rng) + schedule(ARMS, options.units, rng)
    records: dict[str, dict[str, list[dict[str, Any]]]] = {
        name: {"warmup": [], "units": []} for name in ARMS
    }
    for position, name in enumerate(order):
        thread = f"{prefix}/latency/{round_}/{name}/{position}"
        record = await run_unit(arms[name], clients[name], thread, position)
        part = "warmup" if position < options.warmup * len(ARMS) else "units"
        records[name][part].append(record)
    for name, client in clients.items():
        arms[name].finish(client)
    return {"order": order, "arms": records}


async def throughput_phase(
    arms: Mapping[str, Arm], round_: int, options: BenchOptions, rng: random.Random, prefix: str
) -> dict[str, Any]:
    async def run_client(arm: Arm, client: Client, c: int) -> list[dict[str, Any]]:
        done: list[dict[str, Any]] = []
        for k in range(options.per_client):
            thread = f"{prefix}/throughput/{round_}/{arm.name}/c{c}/{k}"
            done.append(await run_unit(arm, client, thread, k))
        return done

    order = list(ARMS)
    rng.shuffle(order)
    blocks: dict[str, Any] = {}
    for name in order:
        arm = arms[name]
        clients = [arm.client("throughput", round_, c) for c in range(options.concurrency)]
        started = time.perf_counter_ns()
        finished = await asyncio.gather(
            *(run_client(arm, client, c) for c, client in enumerate(clients))
        )
        wall = time.perf_counter_ns() - started
        for client in clients:
            arm.finish(client)
        units = [record for records in finished for record in records]
        blocks[name] = {
            "wall_ns": wall,
            "units_per_s": len(units) / (wall / 1e9),
            "units": units,
        }
    return {"order": order, "blocks": blocks}


def _effect(key: str, attempt: int, pid: int) -> EffectRequest:
    from crashcourt.sink.ledger import EffectRequest

    return EffectRequest(
        effect_key=key,
        logical_op=key,
        op_id=f"op-{key}",
        attempt=attempt,
        worker="bench",
        pid=pid,
        thread_id=key,
        node="bench",
        payload_hash=hashlib.sha256(key.encode()).hexdigest(),
    )


def ledger_phase(
    scratch: Path, batch: str, round_: int, options: BenchOptions, rng: random.Random, prefix: str
) -> dict[str, Any]:
    """Ledger appends alone: plain, an idempotent first request, and an idempotent duplicate of a
    logical op applied earlier in the round, interleaved over two ledgers open side by side."""
    from crashcourt.sink.ledger import Ledger, TrialId

    ledgers: dict[str, Ledger] = {}
    for mode in ("plain", "once"):
        name = f"ledger-{mode}-{round_}"
        trial = TrialId(batch=batch, cell=name, trial=round_, seed=f"bench-{name}")
        ledgers[mode] = Ledger(scratch / f"{name}.sqlite", trial, apply_once=mode == "once")
    pid = os.getpid()
    applied: list[str] = []
    # The round opens with a first request, so a duplicate always has an applied op to repeat.
    order = ["once-first"]
    order += schedule(SINK_KINDS, options.warmup, rng)
    order += schedule(SINK_KINDS, options.sink_posts, rng)
    warm = 1 + options.warmup * len(SINK_KINDS)
    appends: dict[str, dict[str, list[int]]] = {k: {"warmup": [], "posts": []} for k in SINK_KINDS}
    try:
        for position, kind in enumerate(order):
            ledger = ledgers["plain" if kind == "plain" else "once"]
            if kind == "once-duplicate":
                key, attempt = applied[rng.randrange(len(applied))], 2
            else:
                key, attempt = f"{prefix}/ledger/{round_}/{kind}/{position}", 1
            started = time.perf_counter_ns()
            row = ledger.append(_effect(key, attempt, pid))
            ns = time.perf_counter_ns() - started
            expected = "duplicate" if kind == "once-duplicate" else "applied"
            if row.get("status") != expected:
                raise BenchError(f"ledger append {position} ({kind}) was {row.get('status')!r}")
            if kind == "once-first":
                applied.append(key)
            appends[kind]["warmup" if position < warm else "posts"].append(ns)
        held = {mode: (ledger.rows(), ledger.verify().ok) for mode, ledger in ledgers.items()}
    finally:
        for ledger in ledgers.values():
            ledger.close()
    statuses = {
        mode: {
            "applied": sum(1 for r in rows if r.get("status") == "applied"),
            "duplicate": sum(1 for r in rows if r.get("status") == "duplicate"),
            "chain_ok": ok,
        }
        for mode, (rows, ok) in held.items()
    }
    return {"order": order, "kinds": appends, "ledgers": statuses}


# ---------------------------------------------------------------------------------------------
# Summary and report.


def _overhead(arm: Mapping[str, int], base: Mapping[str, int]) -> dict[str, float]:
    return {
        "p50_ratio": arm["p50_ns"] / base["p50_ns"] - 1,
        "p99_ratio": arm["p99_ns"] / base["p99_ns"] - 1,
        "p50_delta_ns": arm["p50_ns"] - base["p50_ns"],
        "p99_delta_ns": arm["p99_ns"] - base["p99_ns"],
    }


def summarize_round(round_record: Mapping[str, Any]) -> dict[str, Any]:
    """Per arm: the latency of a unit and of each op and call; the throughput block; and the
    ledger's appends. Overheads are each arm's p50 and p99 against base's in the same round."""
    latency: dict[str, Any] = {}
    for name, parts in round_record["latency"]["arms"].items():
        units = parts["units"]
        calls: dict[str, list[int]] = {}
        for unit in units:
            for call, samples in unit["calls"].items():
                calls.setdefault(call, []).extend(samples)
        latency[name] = {
            "unit": describe([u["unit_ns"] for u in units]),
            "start": describe([u["start_ns"] for u in units]),
            "resume": describe([u["resume_ns"] for u in units]),
            "calls": {call: describe(calls[call]) for call in CALLS if call in calls},
            "calls_per_unit": {
                call: len(calls[call]) / len(units) for call in CALLS if call in calls
            },
            "late_units": sum(1 for u in units if u["late"]),
        }
    for name in ARMS:
        if name != "base":
            latency[name]["overhead"] = _overhead(latency[name]["unit"], latency["base"]["unit"])
    throughput: dict[str, dict[str, Any]] = {
        name: {
            "units": len(block["units"]),
            "wall_ns": block["wall_ns"],
            "units_per_s": block["units_per_s"],
            "unit": describe([u["unit_ns"] for u in block["units"]]),
        }
        for name, block in round_record["throughput"]["blocks"].items()
    }
    base_rate = throughput["base"]["units_per_s"]
    for name in ARMS:
        if name != "base":
            throughput[name]["rate_ratio"] = throughput[name]["units_per_s"] / base_rate - 1
    kinds = round_record["ledger"]["kinds"]
    appends = {kind: describe(parts["posts"]) for kind, parts in kinds.items()}
    return {"latency": latency, "throughput": throughput, "ledger": appends}


def _ms(ns: float) -> str:
    return f"{ns / 1e6:.2f}"


def _us(ns: float) -> str:
    return f"{ns / 1e3:.0f}"


def _pct(ratio: float) -> str:
    return f"{ratio * 100:+.1f}%"


def report(record: Mapping[str, Any]) -> str:
    """bench.md: the runner, the parameters, and per round the tables of the summary."""
    options = record["options"]
    host = record["host"]
    manifest: Mapping[str, Any] = record.get("manifest") or {}
    packages: Mapping[str, Any] = manifest.get("packages") or {}
    images: Mapping[str, Any] = manifest.get("images") or {}
    checks = "all passed" if record["valid"] else "FAILED: " + "; ".join(record["problems"])
    lines = [
        "# Mitigation overhead benchmark",
        "",
        "A measurement of cost, not an experiment: no fault, checker, or prediction. Every",
        f"duration is in `{BENCH_FILE}`. p50 and p99 are nearest-rank; overheads compare each arm",
        "with base in the same round. Three rounds give three values; no interval is computed",
        "from them.",
        "",
        f"- Commit: `{record['git']['sha']}`{' (dirty)' if record['git']['dirty'] else ''}",
        f"- Runner: {host['runner']}; {host['cpu_model']}; {host['cpu_count']} CPUs; "
        f"{host['memory_gib']} GiB; {host['platform']}",
        f"- Image: {host.get('image') or '-'}; Docker {manifest.get('docker_version', '-')}; "
        f"Postgres image `{images.get('postgres', '-')}`; langgraph "
        f"{packages.get('langgraph', '-')}, langgraph-checkpoint-postgres "
        f"{packages.get('langgraph-checkpoint-postgres', '-')}",
        f"- Parameters: {options['rounds']} rounds; latency {options['units']} units per arm after "
        f"{options['warmup']} warm-up; throughput {options['concurrency']} clients x "
        f"{options['per_client']} units; ledger {options['sink_posts']} appends per kind; "
        f"seed {options['seed']}",
        f"- Started {record['started']}, finished {record['finished']}; checks: {checks}",
        "",
        "Arms: base (no fence, plain ledger), F (fence enforced), I (idempotent ledger), FI",
        "(both). A unit is one W1 run: the start op and the resume that posts the charge.",
    ]
    for number, summary in enumerate(record["summary"], start=1):
        latency, throughput, appends = summary["latency"], summary["throughput"], summary["ledger"]
        lines += [
            "",
            f"## Round {number}",
            "",
            "| Arm | n | unit p50 ms | unit p99 ms | mean ms | p50 vs base | p99 vs base "
            "| units/s at concurrency | rate vs base |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for name in ARMS:
            unit, rate = latency[name]["unit"], throughput[name]
            over = latency[name].get("overhead")
            lines.append(
                f"| {name} | {unit['n']} | {_ms(unit['p50_ns'])} | {_ms(unit['p99_ns'])} "
                f"| {_ms(unit['mean_ns'])} | {_pct(over['p50_ratio']) if over else '-'} "
                f"| {_pct(over['p99_ratio']) if over else '-'} | {rate['units_per_s']:.1f} "
                f"| {_pct(rate['rate_ratio']) if 'rate_ratio' in rate else '-'} |"
            )
        lines += [
            "",
            "Calls inside a unit (p50 / p99 in microseconds; calls per unit):",
            "",
            "| Arm | " + " | ".join(CALLS) + " |",
            "| --- | " + " | ".join("---" for _ in CALLS) + " |",
        ]
        for name in ARMS:
            calls, per = latency[name]["calls"], latency[name]["calls_per_unit"]
            cells = [
                f"{_us(calls[c]['p50_ns'])} / {_us(calls[c]['p99_ns'])} ({per[c]:g})"
                if c in calls
                else "-"
                for c in CALLS
            ]
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines += [
            "",
            "Ledger appends alone (microseconds):",
            "",
            "| Kind | n | p50 | p99 | mean |",
            "| --- | --- | --- | --- | --- |",
        ]
        for kind in SINK_KINDS:
            s = appends[kind]
            lines.append(
                f"| {kind} | {s['n']} | {_us(s['p50_ns'])} | {_us(s['p99_ns'])} "
                f"| {_us(s['mean_ns'])} |"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------
# Setup, checks, and the whole run.


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def host_record(probes: Any) -> dict[str, Any]:
    cpus, memory = probes.hardware()
    # GitHub-hosted runners name their image in these two variables, in this case.
    names = ("ImageOS", "ImageVersion")
    image = " ".join(v for v in (os.environ.get(name) for name in names) if v)
    return {
        "runner": probes.runner(),
        "cpu_model": _cpu_model(),
        "cpu_count": cpus,
        "memory_gib": memory,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "image": image or None,
    }


@contextmanager
def prepared_databases(admin_dsn: str) -> Iterator[dict[str, str]]:
    """Set up the benchmark's databases, and remove only those this invocation created.

    The names are reserved for this benchmark. A pre-existing name is a hard refusal, not
    permission to drop anyone else's database or force-disconnect their clients. Cleanup is
    active from the first successful CREATE, including if the second CREATE or setup fails.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    created: list[str] = []
    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
            for name in DATABASES.values():
                try:
                    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                except psycopg.errors.DuplicateDatabase as exc:
                    raise BenchError(
                        f"benchmark database {name!r} already exists; refusing to overwrite it"
                    ) from exc
                created.append(name)
        dsns = {key: make_conninfo(admin_dsn, dbname=name) for key, name in DATABASES.items()}

        async def setup() -> None:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            for dsn in dsns.values():
                async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
                    await saver.setup()

        _run_async(setup())
        fence.install(dsns["fenced"], "enforce")
        yield dsns
    finally:
        if created:
            with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
                for name in reversed(created):
                    conn.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
                    )


def _run_async(coro: Awaitable[Any]) -> Any:
    async def main() -> Any:
        return await coro

    if sys.platform == "win32":  # psycopg's async connections need a selector loop on Windows
        return asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(main())


def check_records(
    record: Mapping[str, Any], dsns: Mapping[str, str], arms: Mapping[str, Arm]
) -> list[str]:
    """What must hold after a fault-free benchmark: every fenced thread owned by its resume's
    epoch and the plain database without a fence; every ledger chain intact, with one applied
    charge per unit and no duplicate, and in the ledger phase exactly the duplicates appended."""
    problems: list[str] = []
    fenced_threads: list[str] = []
    for round_record in record["rounds"]:
        for name in FENCED:
            for part in round_record["latency"]["arms"][name].values():
                fenced_threads += [u["thread"] for u in part]
            block = round_record["throughput"]["blocks"][name]
            fenced_threads += [u["thread"] for u in block["units"]]
    owners = fence.owners(dsns["fenced"], fenced_threads)
    wrong = [t for t in fenced_threads if owners.get(t) != 2]
    if wrong:
        problems.append(f"{len(wrong)} fenced threads not owned by epoch 2, e.g. {wrong[:2]}")
    if fence.installed(dsns["plain"]) is not None:
        problems.append("the plain database has a fence installed")
    if fence.installed(dsns["fenced"]) != "enforce":
        problems.append("the fenced database's fence is not in enforce mode")
    for arm in arms.values():
        for ledger, units in arm.ledgers:
            statuses = [str(r.get("status")) for r in ledger.rows()]
            if statuses != ["applied"] * units:
                problems.append(
                    f"ledger {ledger.trial.cell}: statuses {sorted(set(statuses))} over "
                    f"{len(statuses)} rows, expected {units} applied"
                )
            if not ledger.verify().ok:
                problems.append(f"ledger {ledger.trial.cell}: the hash chain does not verify")
            ledger.close()
    for number, round_record in enumerate(record["rounds"], start=1):
        kinds = round_record["ledger"]["kinds"]
        posted = {kind: sum(len(v) for v in parts.values()) for kind, parts in kinds.items()}
        expected = {
            "plain": {"applied": posted["plain"], "duplicate": 0, "chain_ok": True},
            "once": {
                "applied": posted["once-first"],
                "duplicate": posted["once-duplicate"],
                "chain_ok": True,
            },
        }
        if round_record["ledger"]["ledgers"] != expected:
            problems.append(
                f"round {number}'s ledger phase holds {round_record['ledger']['ledgers']}, "
                f"expected {expected}"
            )
    return problems


async def _measure(
    options: BenchOptions, dsns: Mapping[str, str], scratch: Path, batch: str
) -> tuple[list[dict[str, Any]], dict[str, Arm]]:
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    saver_class = timed_saver_class()
    size = max(4, options.concurrency)
    arms: dict[str, Arm] = {}
    for name in ARMS:
        pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            dsns["fenced" if name in FENCED else "plain"],
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=False,
            check=fence.bind_session,  # as the worker's pool: each checkout binds the run's epoch
            min_size=size,
            max_size=size,
        )
        await pool.open(wait=True, timeout=30)
        arms[name] = Arm(name, pool, saver_class(pool), scratch, batch)
    rng = random.Random(options.seed)
    rounds: list[dict[str, Any]] = []
    try:
        for number in range(1, options.rounds + 1):
            latency = await latency_phase(arms, number, options, rng, batch)
            throughput = await throughput_phase(arms, number, options, rng, batch)
            ledger = ledger_phase(scratch, batch, number, options, rng, batch)
            rounds.append(
                {"round": number, "latency": latency, "throughput": throughput, "ledger": ledger}
            )
            print(f"round {number} of {options.rounds} done", flush=True)
    finally:
        for arm in arms.values():
            await arm.pool.close()
    return rounds, arms


def run_bench(
    options: BenchOptions,
    root: Path,
    *,
    own_database: bool = False,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """The whole benchmark: doctor, the twin databases, the rounds, the checks, the files.
    BenchError if it cannot start or a unit misbehaves; the record says whether every check
    held (`valid`)."""
    from crashcourt.doctor import format_report, run_doctor
    from crashcourt.faults.base import FaultError
    from crashcourt.faults.selftest import require_local_dsn
    from crashcourt.probes import DEFAULT_PG_DSN, SystemProbes

    problems = options.problems()
    if problems:
        raise BenchError("; ".join(problems))
    if options.out.exists():
        raise BenchError(f"{options.out} already exists; a benchmark never overwrites one")
    admin_dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
    if not own_database:
        try:
            require_local_dsn(admin_dsn)
        except FaultError as exc:
            raise BenchError(f"{exc}; pass --i-own-this-database only if it is yours") from exc
    probes = SystemProbes(root)
    doctor = run_doctor(probes)
    if doctor.manifest is None:
        raise BenchError("doctor did not pass:\n" + format_report(doctor))
    manifest = json.loads(doctor.manifest.to_json())
    sha, dirty = probes.git_state()
    started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    batch = f"bench-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}"
    progress(f"{batch}: creating {', '.join(DATABASES.values())}")
    with prepared_databases(admin_dsn) as dsns:
        with tempfile.TemporaryDirectory(prefix="crashcourt-bench-") as scratch:
            rounds, arms = _run_async(_measure(options, dsns, Path(scratch), batch))
            record: dict[str, Any] = {
                "v": BENCH_VERSION,
                "kind": "crashcourt mitigation bench",
                "note": "a measurement of cost, not an experiment and not a batch",
                "batch": batch,
                "options": {k: v for k, v in asdict(options).items() if k != "out"},
                "started": started,
                "finished": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "git": {"sha": sha, "dirty": dirty},
                "manifest": manifest,
                "host": host_record(probes),
                "databases": DATABASES,
                "rounds": rounds,
            }
            record["problems"] = check_records(record, dsns, arms)
    record["valid"] = not record["problems"]
    record["summary"] = [summarize_round(r) for r in rounds]
    options.out.mkdir(parents=True)
    (options.out / BENCH_FILE).write_text(
        json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    (options.out / REPORT_FILE).write_text(report(record), encoding="utf-8")
    return record
