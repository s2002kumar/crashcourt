"""Control C4 evidence for T-06: conformance side by side, and the wrapper's overhead on `put`.

Needs the compose services. From the repository root:

    uv run python research/evidence/t-06/c4.py [conformance]

Writes conformance.md and overhead-<os>.md (for example overhead-linux.md) next to this file;
with `conformance`, only conformance.md.
"""

from __future__ import annotations

import asyncio
import os
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, empty_checkpoint
from langgraph.checkpoint.base.id import uuid6
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from crashcourt.failpoints.saver import wrap
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.probes import DEFAULT_PG_DSN

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))  # the repository root, for tests.c4

from tests.c4 import SaverFactory, fresh_postgres, report  # noqa: E402

DSN = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
HOST = f"{platform.system()} {platform.release()}, Python {platform.python_version()}"
OVERHEAD = f"overhead-{platform.system().lower()}.md"


def run[T](coro: Any) -> T:
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


@asynccontextmanager
async def memory() -> AsyncIterator[BaseCheckpointSaver[Any]]:
    yield InMemorySaver()


@asynccontextmanager
async def sqlite() -> AsyncIterator[BaseCheckpointSaver[Any]]:
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        yield saver


def conformance_section(name: str, factory: SaverFactory) -> list[str]:
    raw = run(report(name, factory, history_dir=None))
    with tempfile.TemporaryDirectory() as histories:
        wrapped = run(report(name, factory, history_dir=Path(histories)))
    lines = [
        f"### {name}",
        "",
        f"Identical: **{'yes' if raw == wrapped else 'NO'}**. "
        f"Conformance level: raw {raw.get('conformance_level')}, "
        f"wrapped {wrapped.get('conformance_level')}.",
        "",
        "| Capability | Raw: detected / passed / failed / skipped "
        "| Wrapped: detected / passed / failed / skipped |",
        "| --- | --- | --- |",
    ]
    for capability in raw["results"]:
        cells = []
        for result in (raw["results"][capability], wrapped["results"][capability]):
            cells.append(
                f"{result['detected']} / {result['tests_passed']} / "
                f"{result['tests_failed']} / {result['tests_skipped']}"
            )
        lines.append(f"| {capability} | {cells[0]} | {cells[1]} |")
    failures = sorted({f for r in raw["results"].values() for f in r.get("failures", [])})
    lines += [
        "",
        f"Failing tests on the raw saver: {', '.join(failures) if failures else 'none'}.",
        "",
    ]
    return lines


def write_conformance() -> None:
    lines = [
        "# Conformance through the failpoint wrapper (T-06, control C4)",
        "",
        f"langgraph-checkpoint-conformance 0.0.2; savers at the uv.lock pins; host {HOST}.",
        "Each suite runs once on the raw saver and once on the same saver wrapped as in a trial: "
        "recording every call to a history writer, with an empty failpoint registry.",
        "Postgres gets a fresh database per capability suite. The comparison is the whole report "
        "(`CapabilityReport.to_dict()` without the name).",
        "",
    ]
    lines += conformance_section("InMemorySaver", memory)
    lines += conformance_section("AsyncSqliteSaver", sqlite)
    lines += conformance_section("AsyncPostgresSaver", fresh_postgres(DSN))
    (HERE / "conformance.md").write_bytes(("\n".join(lines).rstrip() + "\n").encode())


def checkpoint(step: int) -> Checkpoint:
    cp = empty_checkpoint()
    cp["id"] = str(uuid6(clock_seq=step))
    cp["channel_values"] = {"n": step, "messages": ["hello"] * 5}
    cp["channel_versions"] = {"n": str(step + 1), "messages": str(step + 1)}
    return cp


def summarize(samples_ns: list[int]) -> tuple[float, float]:
    ordered = sorted(samples_ns)
    p50 = statistics.median(ordered) / 1000
    p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))] / 1000
    return p50, p99


def rotated(savers: dict[str, Any], step: int) -> list[tuple[str, Any]]:
    """The variants in a different order each step, so no variant always runs first or last."""
    items = list(savers.items())
    shift = step % len(items)
    return items[shift:] + items[:shift]


def interleaved(
    savers: dict[str, Any], n: int, put: Callable[[Any, Any, Checkpoint, int], Any]
) -> dict[str, list[int]]:
    """Alternate the variants call by call, rotating their order, so drift, warm-up, and
    position in the round affect them equally."""
    samples: dict[str, list[int]] = {name: [] for name in savers}
    parents: dict[str, Any] = {
        name: {"configurable": {"thread_id": f"bench-{name}-{time.time_ns()}", "checkpoint_ns": ""}}
        for name in savers
    }
    for step in range(n + 50):
        for name, saver in rotated(savers, step):
            cp = checkpoint(step)
            started = time.perf_counter_ns()
            parents[name] = put(saver, parents[name], cp, step)
            elapsed = time.perf_counter_ns() - started
            if step >= 50:  # warm-up excluded
                samples[name].append(elapsed)
    return samples


def sync_put(saver: Any, config: Any, cp: Checkpoint, step: int) -> Any:
    versions = dict(cp["channel_versions"])
    return saver.put(config, cp, {"source": "loop", "step": step}, versions)


def overhead_rows(title: str, samples: dict[str, list[int]]) -> list[str]:
    raw_p50, raw_p99 = summarize(samples["raw"])
    rows = []
    for name, label in (
        ("raw", "raw"),
        ("bare", "wrapped, no history"),
        ("logged", "wrapped, with history"),
    ):
        p50, p99 = summarize(samples[name])
        change = (
            ""
            if name == "raw"
            else f"{(p50 - raw_p50) / raw_p50:+.1%} / {(p99 - raw_p99) / raw_p99:+.1%}"
        )
        rows.append(
            f"| {title} | {label} | {len(samples[name])} | {p50:.1f} | {p99:.1f} | {change} |"
        )
    return rows


def write_overhead(n_memory: int = 3000, n_postgres: int = 500) -> None:
    rows: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        run_id = RunIdentity(batch="bench", cell="c4-overhead", trial=0, seed="0")
        with HistoryWriter(Path(tmp) / "memory.jsonl", run=run_id, proc="bench") as history:
            savers = {
                "raw": InMemorySaver(),
                "bare": wrap(InMemorySaver()),
                "logged": wrap(InMemorySaver(), history=history),
            }
            rows += overhead_rows("InMemorySaver.put", interleaved(savers, n_memory, sync_put))
        with (
            HistoryWriter(Path(tmp) / "pg.jsonl", run=run_id, proc="bench") as history,
            PostgresSaver.from_conn_string(DSN) as raw,
            PostgresSaver.from_conn_string(DSN) as bare_inner,
            PostgresSaver.from_conn_string(DSN) as logged_inner,
        ):
            raw.setup()
            savers = {
                "raw": raw,
                "bare": wrap(bare_inner),
                "logged": wrap(logged_inner, history=history),
            }
            rows += overhead_rows("PostgresSaver.put", interleaved(savers, n_postgres, sync_put))

        async def async_pg() -> dict[str, list[int]]:
            samples: dict[str, list[int]] = {"raw": [], "bare": [], "logged": []}
            async with (
                AsyncPostgresSaver.from_conn_string(DSN) as raw_a,
                AsyncPostgresSaver.from_conn_string(DSN) as bare_a,
                AsyncPostgresSaver.from_conn_string(DSN) as logged_a,
            ):
                with HistoryWriter(Path(tmp) / "apg.jsonl", run=run_id, proc="bench") as history:
                    savers = {
                        "raw": raw_a,
                        "bare": wrap(bare_a),
                        "logged": wrap(logged_a, history=history),
                    }
                    parents: dict[str, Any] = {
                        name: {
                            "configurable": {
                                "thread_id": f"abench-{name}-{time.time_ns()}",
                                "checkpoint_ns": "",
                            }
                        }
                        for name in savers
                    }
                    for step in range(n_postgres + 50):
                        for name, saver in rotated(savers, step):
                            cp = checkpoint(step)
                            started = time.perf_counter_ns()
                            parents[name] = await saver.aput(
                                parents[name],
                                cp,
                                {"source": "loop", "step": step},
                                dict(cp["channel_versions"]),
                            )
                            if step >= 50:
                                samples[name].append(time.perf_counter_ns() - started)
            return samples

        rows += overhead_rows("AsyncPostgresSaver.aput", run(async_pg()))
    lines = [
        "# Failpoint wrapper overhead on `put` (T-06, control C4)",
        "",
        f"Host: {HOST}; Postgres 18.6 in Docker, reached directly at "
        f"{DSN.split('@')[-1]} (no Toxiproxy).",
        "The three variants run interleaved, one call each per round in a rotating order, "
        "after 50 warm-up rounds.",
        '"With history" writes the `saver.put.call` and `.return` events to a JSONL file, '
        "as a worker does.",
        "",
        "| Operation | Variant | Calls | p50 (µs) | p99 (µs) | Change vs raw, p50 / p99 |",
        "| --- | --- | --- | --- | --- | --- |",
        *rows,
    ]
    (HERE / OVERHEAD).write_bytes(("\n".join(lines) + "\n").encode())


if __name__ == "__main__":
    write_conformance()
    print((HERE / "conformance.md").read_text(encoding="utf-8"))
    if sys.argv[1:] != ["conformance"]:
        write_overhead()
        print((HERE / OVERHEAD).read_text(encoding="utf-8"))
