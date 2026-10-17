"""Control C9, the sequential reference, as the pre-registration states it
(crashcourt.experiment.controls.C9_REQUIREMENT).

    python -m tests.controls.c9 --batches <batch dir>... --report <path>.json

Each batch is one canonical batch of a C9 cell (C1-w1-sync or C1-w5-sync), as `crashcourt run`
wrote it. For every trial, the same operation sequence (the batch's scenario.json, in dispatch
order, on the trial's own thread id) runs on InMemorySaver in this process, behind the failpoint
wrapper and the workload's harness as a worker runs it, with a sink double over T-08's ledger.
Its final state, the head checkpoint's channel values, is compared with the Postgres run's: the
head of the thread's lineage in the trial's second snapshot, each channel decoded by the frozen
checker's own reader (crashcourt.checker.lineage). Both sides are normalized the same way (the
trial's thread id replaced, messages reduced to type, id, and content), and must be equal.

The report records every trial that differed or could not be decoded, the checker whose reader
decoded the Postgres side, the manifest the batches ran with, and the sha256 of this driver and
the scenarios, so the gate can tell whether it describes the committed files. It is written
whatever the result; `passed` is computed from the counts. Linux only, from a clean checkout,
on batches run through the gate (an instrument cell a draft can authorize).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from crashcourt.checker.decode import DecodeError
from crashcourt.checker.freeze import FROZEN_PATH, verify_checker
from crashcourt.checker.lineage import ChannelReader, build_lineages
from crashcourt.checker.load import load_trial
from crashcourt.clauses.registry import registry_sha256
from crashcourt.experiment import gitproof
from crashcourt.experiment.controls import (
    C9_DRIVER,
    C9_REQUIREMENT,
    CheckerUsed,
    ReferenceBatch,
    ReferenceReport,
)
from crashcourt.experiment.fingerprint import unexplained
from crashcourt.failpoints.context import operation
from crashcourt.failpoints.saver import wrap
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.manifest import Manifest
from crashcourt.orchestrator.declared import trial_scenario
from crashcourt.sink.ledger import EffectRequest, Ledger, TrialId
from crashcourt.spec.scenario import START, Scenario, parse_scenario
from crashcourt.workloads import w1_approval, w5_continuation
from crashcourt.workloads.harness import Harness

ROOT = Path(__file__).resolve().parents[2]
THREAD = "<thread>"
#: The channels compared per workload: the state the effect model and the client read.
CHANNELS: dict[str, tuple[str, ...]] = {
    "w1": ("order", "plan", "resume", "decision", "executed"),
    "w5": ("messages",),
}
BUILDERS = {"w1": w1_approval.build, "w5": w5_continuation.build}


class C9Refused(Exception):
    """C9 cannot produce a report that would describe the committed files."""


def normalize(value: Any, thread_id: str) -> Any:
    """A JSON-comparable form of a channel value, with the trial's thread id replaced."""
    if isinstance(value, str):
        return value.replace(thread_id, THREAD)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): normalize(v, thread_id) for k, v in sorted(value.items())}  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return [normalize(v, thread_id) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if hasattr(value, "type") and hasattr(value, "content"):  # a langchain message
        return [
            normalize(str(value.type), thread_id),
            normalize(str(getattr(value, "id", None)), thread_id),
            normalize(str(value.content), thread_id),
        ]
    raise TypeError(f"cannot normalize {type(value).__name__}")


def final_state(values: Mapping[str, Any], workload: str, thread_id: str) -> dict[str, Any]:
    """The compared channels of a final state, normalized; an absent channel is recorded so."""
    return {
        channel: normalize(values[channel], thread_id) if channel in values else "<absent>"
        for channel in CHANNELS[workload]
    }


class _LedgerSink:
    """The sink's two calls, straight to a ledger file in this process."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def post_effect(self, trial: TrialId, effect: EffectRequest) -> dict[str, Any]:
        return {"sink_seq": self.ledger.append(effect)["sink_seq"]}

    def next_attempt(self, trial: TrialId, key: str) -> int:
        return self.ledger.next_attempt(key)


def dispatch_order(scenario: Scenario) -> list[Any]:
    """The ops in the order the runner dispatches them: each after the op it names."""
    ordered: list[Any] = []
    pending = list(scenario.ops)
    done: set[str] = set()
    while pending:
        ready = [op for op in pending if op.after == START or op.after in done]
        if not ready:
            raise C9Refused("the scenario's ops do not form a dispatch order")
        for op in ready:
            ordered.append(op)
            done.add(op.id)
            pending.remove(op)
    return ordered


def reference_state(
    scenario: Scenario, thread_id: str, identity: RunIdentity, scratch: Path
) -> dict[str, Any]:
    """The same operations on InMemorySaver in this process: the head's channel values."""
    scratch.mkdir(parents=True, exist_ok=True)
    history = HistoryWriter(scratch / "reference.jsonl", run=identity, proc="w1")
    ledger = Ledger(
        scratch / "reference.sqlite",
        TrialId(identity.batch, identity.cell, identity.trial, identity.seed),
    )
    saver = InMemorySaver()
    try:
        graph = BUILDERS[scenario.workload](
            wrap(saver, history=history), Harness(history, _LedgerSink(ledger))
        )
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        async def run() -> None:
            for op in dispatch_order(scenario):
                payload = Command(resume=op.resume) if op.resume is not None else op.input
                with operation(op.id):
                    await graph.ainvoke(payload, config, durability="sync")

        asyncio.run(run())
        found = saver.get_tuple(config)
        if found is None:
            raise C9Refused(f"the reference run left no checkpoint on {thread_id}")
        return final_state(found.checkpoint["channel_values"], scenario.workload, thread_id)
    finally:
        ledger.close()
        history.close()


def postgres_state(batch_dir: Path, trial: int, workload: str) -> tuple[str, dict[str, Any]]:
    """The Postgres run's final state: the head of the thread's lineage in the trial's second
    snapshot, decoded by the frozen checker's reader. Returns (thread id, state)."""
    facts = trial_scenario(batch_dir, trial, evidence="control")
    [thread_id] = facts.threads
    directory = batch_dir / facts.cell / f"trial-{trial:05d}"
    artifacts = load_trial(directory, facts)
    if artifacts.snapshots is None:
        raise DecodeError("the trial has no snapshot")
    snapshot = artifacts.snapshots.second.canonical()
    lineage = build_lineages(snapshot).get((thread_id, ""))
    if lineage is None or lineage.head is None:
        raise DecodeError(f"the snapshot holds no lineage for {thread_id}")
    reader = ChannelReader(snapshot)
    row = lineage.rows[lineage.head]
    values: dict[str, Any] = {}
    for channel in CHANNELS[workload]:
        present, value = reader.value(row, channel)
        if present:
            values[channel] = value
    return thread_id, final_state(values, workload, thread_id)


def compare_batch(batch_dir: Path, scratch: Path) -> tuple[ReferenceBatch, Manifest]:
    run: dict[str, Any] = json.loads((batch_dir / "run.json").read_text(encoding="utf-8"))
    batch: dict[str, Any] = json.loads((batch_dir / "batch.json").read_text(encoding="utf-8"))
    raw = (batch_dir / "manifest.json").read_bytes()
    manifest = Manifest.from_json(raw.decode("utf-8"))
    scenario = parse_scenario(json.loads((batch_dir / "scenario.json").read_text("utf-8")))
    cell = str(run["cell"])
    if scenario.schedule or scenario.toxics or len(scenario.workers) != 1:
        raise C9Refused(f"{batch_dir} is not a single-worker, fault-free batch")
    if scenario.workload not in CHANNELS:
        raise C9Refused(f"C9 has no reference for workload {scenario.workload}")
    trials = int(run["trials"])
    seeds: dict[str, str] = run["trial_seeds"]
    equal = 0
    differing: dict[str, str] = {}
    undecodable: dict[str, str] = {}
    for n in range(1, trials + 1):
        try:
            thread_id, actual = postgres_state(batch_dir, n, scenario.workload)
        except Exception as exc:  # DecodeError, ShapeError, missing records  # noqa: BLE001
            undecodable[str(n)] = f"{type(exc).__name__}: {exc}"
            continue
        identity = RunIdentity(str(run["batch"]), cell, n, seeds[str(n)])
        expected = reference_state(scenario, thread_id, identity, scratch / f"trial-{n:05d}")
        if actual == expected:
            equal += 1
        else:
            differing[str(n)] = json.dumps(
                {"postgres": actual, "reference": expected}, sort_keys=True, ensure_ascii=False
            )
    result = ReferenceBatch(
        cell=cell,
        scenario=str(run["scenario"]["file"]),
        scenario_sha256=str(batch["scenario_sha256"]),
        batch=str(run["batch"]),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        authorized=(batch_dir / "authorization.json").is_file(),
        trials=trials,
        equal=equal,
        differing=differing,
        undecodable=undecodable,
    )
    return result, manifest


def sources(root: Path, scenarios: Sequence[str]) -> dict[str, str]:
    """sha256 of each source as committed at HEAD; refused when the working copy differs."""
    head = gitproof.head(root)
    found: dict[str, str] = {}
    for path in (C9_DRIVER, *sorted(scenarios)):
        committed = gitproof.blob_sha256(root, head, path)
        if committed is None:
            raise C9Refused(f"{path} is not committed")
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != committed:
            raise C9Refused(f"{path} differs from HEAD; C9 runs only what is committed")
        found[path] = committed
    return found


def run_c9(batch_dirs: Sequence[Path], scratch: Path) -> ReferenceReport:
    registry_hash = registry_sha256(ROOT / "clauses")
    frozen_md = FROZEN_PATH.read_bytes()
    frozen = verify_checker(frozen_md.decode("utf-8"), registry_hash)
    results: list[ReferenceBatch] = []
    manifests: list[Manifest] = []
    for d in batch_dirs:
        result, manifest = compare_batch(d, scratch / d.name)
        results.append(result)
        manifests.append(manifest)
        print(f"{result.cell}: {result.equal} of {result.trials} trials equal", flush=True)
    if not results:
        raise C9Refused("no batches")
    for other in manifests[1:]:
        differing = unexplained(manifests[0], other, ())
        if differing:
            raise C9Refused(f"the batches ran in different environments: {sorted(differing)}")
    # A dirty checkout is not refused here: the manifest records it, and the gate's judge refuses
    # a report whose manifest is not the pre-registered environment (git_dirty false among it).
    passed = all(b.equal == b.trials and not b.differing and not b.undecodable for b in results)
    return ReferenceReport(
        control="C9",
        requirement=C9_REQUIREMENT,
        checker=CheckerUsed(
            version=frozen.checker_version,
            checker_sha256=frozen.checker_sha256,
            frozen_md_sha256=hashlib.sha256(frozen_md).hexdigest(),
            registry_sha256=frozen.registry_sha256,
        ),
        run_commit=manifests[0].git_sha,
        sources=sources(ROOT, [b.scenario for b in results]),
        manifest=json.loads(manifests[0].to_json()),
        batches=tuple(results),
        passed=passed,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.controls.c9")
    parser.add_argument("--batches", type=Path, nargs="+", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="c9-") as scratch:
        try:
            report = run_c9(args.batches, Path(scratch))
        except C9Refused as exc:
            print(f"C9 refused: {exc}")
            return 2
    args.report.write_bytes(report.to_json().encode("utf-8"))
    print(f"C9 {'passed' if report.passed else 'FAILED'}; wrote {args.report}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
