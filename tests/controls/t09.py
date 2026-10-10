"""Run the pre-freeze validation on T-09's real fault-free batches.

Usage (from the repository root):

    uv run python -m tests.controls.t09 <t09-data-dir> <ledger-copy-dir> <report-dir> [--dsn DSN]

T-09's trial directories (T-07's layout) hold the histories, trial.json, and db-rows.jsonl, an
export of the saver rows. They do not yet hold the post-trial files the checker reads, so this
adapter reconstructs them, and the report lists each reconstruction:

- ledger.json: the trial's ledger rows read from a copy of the sink's per-trial SQLite file, and
  the chain verified by T-08's own Ledger.verify;
- snapshot.json: the first snapshot is T-09's db-rows.jsonl export, the second a dump of the same
  threads from the live database now (the two must agree, as trigger 7 requires); the metadata
  (settings, postmaster start) comes from the live dump;
- pre-trial emptiness: not recorded by the runner; reconstructed as zero (thread ids are scoped to
  the trial, so no earlier trial shares them), and listed as unverified;
- sink-health.json: the batch's /health reading from run.json, as the reading after the trial.

The draft landmark table is applied as if audited, so that the checker's verdicts are what they
would be once it is. Nothing here changes a predicate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from crashcourt.checker.artifacts import (
    ArmSpec,
    BlobRow,
    CheckpointRow,
    FaultSpec,
    LandmarkTable,
    LedgerExport,
    LedgerRow,
    OpSpec,
    SinkHealth,
    Snapshot,
    SnapshotMeta,
    SnapshotPair,
    TrialArtifacts,
    TrialScenario,
    WriteRow,
)
from crashcourt.checker.engine import check_trial
from crashcourt.checker.landmarks import locate, occurrences, repository_tables
from crashcourt.checker.load import load_trial
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256
from crashcourt.history.merge import group_by_process
from crashcourt.sink.ledger import Ledger, TrialId
from crashcourt.snapshot.dump import EPOCH, redact, take
from tests.controls.prefreeze import QUIET, W1_MUTANTS
from tests.mutants import artifacts as M

DSN = "postgresql://crashcourt:crashcourt@127.0.0.1:55452/crashcourt"
RECONSTRUCTED = (
    "ledger rows from a copy of the sink's SQLite file, verified with T-08's Ledger.verify",
    "first snapshot from db-rows.jsonl, second from a live dump; metadata from the live dump",
    "pre-trial emptiness not recorded by the runner; set to zero (thread ids are trial-scoped)",
    "the sink reading after the trial taken from the batch's /health reading in run.json",
    "the draft landmark table applied as if audited",
)


def _ns(text: str | None) -> int | None:
    if text is None:
        return None
    return (
        (datetime.fromisoformat(text) - EPOCH)
        // __import__("datetime").timedelta(microseconds=1)
        * 1000
    )


def _blob(hexed: str | None) -> bytes | None:
    return bytes.fromhex(hexed) if hexed is not None else None


def export_snapshot(path: Path, meta: SnapshotMeta) -> Snapshot:
    checkpoints: list[CheckpointRow] = []
    blobs: list[BlobRow] = []
    writes: list[WriteRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        r: dict[str, Any] = json.loads(line)
        common = {
            "thread_id": r["thread_id"],
            "checkpoint_ns": r["checkpoint_ns"],
            "xmin": r["xmin"],
            "commit_ns": _ns(r["commit_ts"]),
        }
        if r["table"] == "checkpoints":
            checkpoints.append(
                CheckpointRow(
                    **common,
                    checkpoint_id=r["checkpoint_id"],
                    parent_checkpoint_id=r["parent_checkpoint_id"],
                    checkpoint=r["checkpoint"],
                    metadata=r["metadata"],
                )
            )
        elif r["table"] == "checkpoint_blobs":
            blobs.append(
                BlobRow(
                    **common,
                    channel=r["channel"],
                    version=r["version"],
                    type=r["type"],
                    blob=_blob(r["blob"]),
                )
            )
        else:
            writes.append(
                WriteRow(
                    **common,
                    checkpoint_id=r["checkpoint_id"],
                    task_id=r["task_id"],
                    task_path=r["task_path"],
                    idx=r["idx"],
                    channel=r["channel"],
                    type=r["type"],
                    blob=_blob(r["blob"]),
                )
            )
    return Snapshot(
        meta=meta,
        checkpoints=tuple(
            sorted(checkpoints, key=lambda c: (c.thread_id, c.checkpoint_ns, c.checkpoint_id))
        ),
        blobs=tuple(
            sorted(blobs, key=lambda b: (b.thread_id, b.checkpoint_ns, b.channel, b.version))
        ),
        writes=tuple(
            sorted(
                writes,
                key=lambda w: (w.thread_id, w.checkpoint_ns, w.checkpoint_id, w.task_id, w.idx),
            )
        ),
    )


def facts(batch: Path, trial: int, table: LandmarkTable, dsn: str) -> TrialScenario:
    scenario: dict[str, Any] = json.loads((batch / "scenario.json").read_text(encoding="utf-8"))
    run: dict[str, Any] = json.loads((batch / "run.json").read_text(encoding="utf-8"))
    record: dict[str, Any] = json.loads((batch / "batch.json").read_text(encoding="utf-8"))
    manifest: dict[str, Any] = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    batch_id, cell, workload = run["batch"], run["cell"], scenario["workload"]

    def thread(name: str) -> str:
        return f"{batch_id}/{cell}/{trial}/{name}"

    ops = tuple(
        OpSpec(
            op_id=o["id"],
            kind="resume" if o["resume"] else ("turn" if workload == "w5" else "start"),
            thread_id=thread(o["thread"]),
            logical_op=o["id"],
            resume=o["resume"],
        )
        for o in scenario["ops"]
    )
    arms: list[ArmSpec] = []
    for step in scenario["schedule"]:
        on, do = step["on"], step["do"]
        if "fp" not in on:
            continue
        method = str(on["fp"]).split(".", 1)[0]
        landmark = next((s.id for s in table.landmarks if s.method == method), None)
        arms.append(
            ArmSpec(
                point=on["fp"],
                action="pause" if "pause" in do else ("exit" if "exit" in do else "signal"),
                worker=on["worker"],
                op_id=on.get("op"),
                occurrence=on.get("occurrence", 1),
                landmark=landmark,
            )
        )
    target = scenario["target"]
    return TrialScenario(
        batch=batch_id,
        cell=cell,
        trial=trial,
        provenance="run",
        manifest_sha256=record["manifest_sha256"],
        evidence="control",
        workload=workload,
        durability=target["durability"],
        target="AsyncPostgresSaver"
        if (target["saver"], target["driver"]) == ("postgres", "async")
        else "PostgresSaver",
        threads=tuple(sorted({o.thread_id for o in ops})),
        ops=ops,
        arms=tuple(arms),
        faults=tuple(
            FaultSpec(kind="pause", arm=i) for i, a in enumerate(arms) if a.action == "pause"
        ),
        landmarks=table,
        oracle_dsn=redact(dsn),
        runner=manifest["runner"],
        sink_source_sha256=run["sink"]["expected_source_sha256"],
        proxies={route["proxy"]: worker for worker, route in run["routes"].items()},
    )


def reconstruct(
    batch: Path, trial: int, ledgers: Path, table: LandmarkTable, dsn: str
) -> TrialArtifacts:
    scenario = facts(batch, trial, table, dsn)
    directory = batch / scenario.cell / f"trial-{trial:05d}"
    artifacts = load_trial(directory, scenario)
    run: dict[str, Any] = json.loads((batch / "run.json").read_text(encoding="utf-8"))
    seed = run["trial_seeds"][str(trial)]
    ledger_file = ledgers / scenario.batch / scenario.cell / f"trial-{trial:05d}.sqlite"
    rows: list[LedgerRow] = []
    verified = False
    if ledger_file.is_file():
        ledger = Ledger(ledger_file, TrialId(scenario.batch, scenario.cell, trial, seed))
        try:
            rows = [LedgerRow.model_validate(r) for r in ledger.rows()]
            verified = ledger.verify().ok
        finally:
            ledger.close()
    orchestrator = next(h for h in artifacts.histories if h.proc == "orchestrator")
    after = len(orchestrator.events) - 1
    live = take(dsn, scenario.threads, after_orchestrator_seq=after)
    exported = export_snapshot(directory / "db-rows.jsonl", live.meta)
    health = run["sink"]["health"]
    return artifacts.model_copy(
        update={
            "ledger": LedgerExport(rows=tuple(rows), verify_ok=verified)
            if ledger_file.is_file()
            else None,
            "snapshots": SnapshotPair(
                first=exported, second=live, pretrial_rows=dict.fromkeys(scenario.threads, 0)
            ),
            "sink_health": (
                SinkHealth(
                    when="after",
                    hooks_enabled=bool(health["test_hooks"]),
                    source_sha256=health["source_sha256"],
                    open_trial=None,
                ),
            ),
        }
    )


@dataclass
class BatchReport:
    batch: str
    cell: str
    trials: int = 0
    invalid: list[str] = field(default_factory=list[str])
    outcomes: Counter[str] = field(default_factory=Counter[str])
    false_anomalies: list[str] = field(default_factory=list[str])
    landmarks: dict[str, int] = field(default_factory=dict[str, int])
    landmark_problems: list[str] = field(default_factory=list[str])
    #: Landmarks no call matched in some trial (expected for another workload's landmarks).
    absent: Counter[str] = field(default_factory=Counter[str])
    mutants: dict[str, str] = field(default_factory=dict[str, str])
    observations: Counter[str] = field(default_factory=Counter[str])


def run_batch(
    batch: Path, ledgers: Path, table: LandmarkTable, dsn: str, registry: ClauseRegistry, sha: str
) -> BatchReport:
    run: dict[str, Any] = json.loads((batch / "run.json").read_text(encoding="utf-8"))
    report = BatchReport(batch=run["batch"], cell=run["cell"])
    trials = sorted(int(k) for k in run["trial_seeds"])
    sites_per_trial = []
    first: TrialArtifacts | None = None
    fork_cell = report.cell == "w5-concurrent"
    for trial in trials:
        artifacts = reconstruct(batch, trial, ledgers, table, dsn)
        first = first or artifacts
        report.trials += 1
        sites, problems = locate(table, group_by_process(h.events for h in artifacts.histories))
        sites_per_trial.append(sites)
        report.landmark_problems.extend(
            f"trial {trial}: {p}" for p in problems if "no call matches" not in p
        )
        report.absent.update(p.split(":")[0] for p in problems if "no call matches" in p)
        out = check_trial(artifacts, registry, registry_sha256=sha)
        if out.report.invalid is not None:
            report.invalid.append(
                f"trial {trial}: trigger {out.report.invalid.trigger}: {out.report.invalid.reason}"
            )
            continue
        for note in (*out.report.observations, *out.report.harness_investigations):
            report.observations[" ".join(note.split()[:4])] += 1
        for r in out.result.results if out.result else ():
            report.outcomes[f"{r.invariant} {r.outcome} {r.clause or '-'}"] += 1
            documented = report.cell == "w1-misuse" and (r.invariant, r.outcome, r.clause) == (
                "I4",
                "EXPECTED",
                "LG-INT-RERUN",
            )
            fork = fork_cell and r.invariant in {"I5a", "I6a"} and r.outcome == "GAP"
            if r.outcome not in QUIET and not documented and not fork:
                report.false_anomalies.append(
                    f"trial {trial}: {r.invariant} {r.outcome} ({r.clause}): {r.reason}"
                )
        if fork_cell:
            got = {r.invariant: r.outcome for r in (out.result.results if out.result else ())}
            if got.get("I5a") != "GAP" or got.get("I6a") != "GAP":
                report.false_anomalies.append(f"trial {trial}: C2 fork not detected: {got}")
    stable, problems = occurrences(sites_per_trial)
    report.landmarks = {f"{k[0]} {k[1]} {k[2]}": v for k, v in stable.items()}
    report.landmark_problems.extend(problems)
    if first is not None and report.cell in {"w1-approve", "w5-concurrent"}:
        mutants = (
            W1_MUTANTS
            if report.cell == "w1-approve"
            else {
                "AM2": W1_MUTANTS["AM2"],
                "AM4": (lambda a: M.am4_delete_blob(a, "messages"), ("I5c", "GAP", None)),
            }
        )
        for label, (mutant, expected) in mutants.items():
            subject = M.am10_reorder(first, "w1") if label == "AM10" else mutant(first)
            out = check_trial(subject, registry, registry_sha256=sha)
            if isinstance(expected, int):
                got_trigger = out.report.invalid.trigger if out.report.invalid else None
                report.mutants[label] = (
                    "ok"
                    if got_trigger == expected
                    else f"trigger {got_trigger}, expected {expected}"
                )
            else:
                got = {
                    r.invariant: (r.outcome, r.clause)
                    for r in (out.result.results if out.result else ())
                }
                invalid = out.report.invalid.reason if out.report.invalid else None
                report.mutants[label] = (
                    "ok"
                    if got.get(expected[0]) == expected[1:]
                    else f"{expected[0]} {got.get(expected[0])} (invalid: {invalid}), "
                    f"expected {expected[1:]}"
                )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("ledgers", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--dsn", default=DSN)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    registry, sha = ClauseRegistry.load(root / "clauses"), registry_sha256(root / "clauses")
    (draft,) = repository_tables()
    table = draft.model_copy(update={"status": "audited"})
    reports = [
        run_batch(batch, args.ledgers, table, args.dsn, registry, sha)
        for batch in sorted(p for p in args.data.iterdir() if p.is_dir())
    ]
    args.out.mkdir(parents=True, exist_ok=True)
    data = {
        "reconstructed": RECONSTRUCTED,
        "registry_sha256": sha,
        "batches": [
            {
                **{
                    k: v
                    for k, v in vars(r).items()
                    if k not in {"outcomes", "observations", "absent"}
                },
                "outcomes": dict(sorted(r.outcomes.items())),
                "observations": dict(sorted(r.observations.items())),
                "absent": dict(sorted(r.absent.items())),
            }
            for r in reports
        ],
    }
    (args.out / "validation.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    failed = any(
        r.invalid or r.false_anomalies or any(v != "ok" for v in r.mutants.values())
        for r in reports
    )
    for r in reports:
        bad = [m for m, v in r.mutants.items() if v != "ok"]
        print(
            f"{r.cell}: {r.trials} trials, {len(r.invalid)} invalid, "
            f"{len(r.false_anomalies)} false anomalies, "
            f"{len(r.landmark_problems)} landmark problems, absent {dict(r.absent)}, "
            f"mutants failing: {bad or 'none'}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["defaultdict", "main"]
