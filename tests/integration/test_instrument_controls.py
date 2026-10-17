"""The instrument cells' shapes on the real stack, judged by the frozen checker: C3 (failpoints
armed with action none and a zero-latency toxic) on W1 and W5, K1a (langgraph#8039: a kill at
L-EXW, then the replacement takes the resume over), and K3 (langgraph#8796, held: a stale worker
held at L-T1 while another takes the turn over, then released).

Controls and published replications only: never a new-evidence cell. Each trial is compared with
the pre-registration's prediction for the cell of the same shape, read from the committed draft,
outcomes and observations alike. Linux only, like `crashcourt run`; CI runs it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from crashcourt.checker.engine import check_trial
from crashcourt.checker.load import load_trial, write_outputs
from crashcourt.checker.status import EvidenceClass
from crashcourt.clauses.registry import ClauseRegistry, registry_sha256
from crashcourt.experiment.classify import NotClassified, classify_batch
from crashcourt.experiment.observe import mismatch
from crashcourt.experiment.preregistration import EVERY_INVARIANT, PREREGISTRATION_PATH, parse
from crashcourt.experiment.verify import TrialFiles, read_trials
from crashcourt.history.reader import read_history
from crashcourt.orchestrator.declared import trial_scenario
from crashcourt.orchestrator.runner import BatchSummary, RunOptions, Settings, run_batch
from crashcourt.spec.scenario import load_scenario

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="crashcourt run needs Linux; CI runs it"),
]

REPO = Path(__file__).resolve().parents[2]
SHAPES = REPO / "tests" / "integration" / "scenarios"
REGISTRY = ClauseRegistry.load(REPO / "clauses")
REGISTRY_SHA = registry_sha256(REPO / "clauses")
PREREG = parse((REPO / PREREGISTRATION_PATH).read_text(encoding="utf-8"))
TRIALS = 2


def judged(
    tmp: Path,
    name: str,
    evidence: EvidenceClass = "control",
    replication_of: str | None = None,
) -> tuple[BatchSummary, list[TrialFiles]]:
    """A batch of the shape, every trial valid, judged by the frozen checker (outputs written)."""
    path = SHAPES / f"t11-{name}.yaml"
    summary = run_batch(
        load_scenario(path),
        RunOptions(trials=TRIALS, out=tmp),
        Settings.from_environment(REPO),
        source=path,
    )
    files: list[TrialFiles] = []
    for trial in summary.trials:
        assert trial.valid, trial.reasons
        facts = trial_scenario(
            summary.directory, trial.number, evidence=evidence, replication_of=replication_of
        )
        output = check_trial(
            load_trial(trial.directory, facts), REGISTRY, registry_sha256=REGISTRY_SHA
        )
        assert output.report.invalid is None, output.report.invalid
        write_outputs(trial.directory, output)
        result = output.result_json()
        files.append(
            TrialFiles(
                trial.number,
                trial.directory,
                json.loads(output.report_json()),
                json.loads(result) if result is not None else None,
            )
        )
    return summary, files


def as_predicted(cell_id: str, trial: TrialFiles) -> list[str]:
    """Where the trial differs from the pre-registered cell's outcomes and observations."""
    cell = PREREG.cell(cell_id)
    results = {r["invariant"]: r["outcome"] for r in (trial.result or {}).get("results", [])}
    not_exercised = {n["invariant"] for n in trial.checker.get("not_exercised", [])}
    named = {p.invariant for p in cell.predicted}
    found: list[str] = []
    for p in cell.predicted:
        names = (
            [i for i in {*results, *not_exercised} if i not in named]
            if p.invariant == EVERY_INVARIANT
            else [p.invariant]
        )
        for invariant in names:
            value = results.get(invariant, "NOT_EXERCISED" if invariant in not_exercised else None)
            if value not in p.expect:
                found.append(f"{invariant} is {value}, predicted {' or '.join(p.expect)}")
    for o in cell.observations:
        if (problem := mismatch(o, trial)) is not None:
            found.append(f"{o.label()}: {problem}")
    return found


def held_takeover(directory: Path, op: str = "turn-1") -> list[str]:
    """Where a trial departs from K3's schedule, read from its records alone: each process's own
    seq order, and the records' references to each other, never a clock. The chain, each link a
    recorded fact or one of crashcourt.history.merge's edges:

    A's hold at L-T1 (w1's fp.hit) precedes the redelivery that names it (`held`); the redelivery
    precedes B's events on the op (dispatch edge); the B event `client.ok` names precedes it
    (result edge); `client.ok` precedes the release of A's hold (the orchestrator's seq); the
    release precedes A's `fp.action` for that hit (barrier edge); and that precedes A's
    acknowledgment of its held put (w1's seq). A does nothing new while held. B continues from the
    checkpoint A's held put extends, so A's write, acknowledged after B answered, is stale."""

    def history(proc: str) -> list[Any]:
        return sorted(read_history(directory / f"{proc}.jsonl").events, key=lambda e: e.seq)

    orch, a, b = history("orchestrator"), history("w1"), history("w2")
    found: list[str] = []
    holds = [
        e
        for e in a
        if (e.kind, e.fp, e.op_id) == ("fp.hit", "put.before", op)
        and (e.data.get("action"), e.data.get("occurrence")) == ("pause", 3)
    ]
    if len(holds) != 1:
        return [f"w1 has {len(holds)} holds at L-T1, not 1"]
    hold = holds[0]
    resumed = [e for e in a if e.kind == "fp.action" and e.data.get("hit_seq") == hold.seq]
    if len(resumed) != 1:
        return [f"w1 resumed from its hold {len(resumed)} times, not once"]
    resume = resumed[0]
    acted = [
        e.kind
        for e in a
        if hold.seq < e.seq < resume.seq
        and (e.kind.endswith(".call") or e.kind.startswith(("node.", "effect.")))
    ]
    if acted:
        found.append(f"w1 acted while held: {acted}")
    acked = [
        e
        for e in a
        if (e.kind, e.checkpoint_id, e.status) == ("saver.put.return", hold.checkpoint_id, "ok")
    ]
    if len(acked) != 1 or acked[0].seq < resume.seq:
        found.append("w1's held put was not acknowledged once, after its hold ended")

    def only(kind: str, **data: object) -> Any:
        hits = [
            e
            for e in orch
            if e.kind == kind
            and e.op_id in (op, None)
            and all(e.data.get(k) == v for k, v in data.items())
        ]
        if len(hits) != 1:
            raise AssertionError(f"{len(hits)} {kind} {data} in the orchestrator's history")
        return hits[0]

    send = only("dispatch.send", to="w1")
    redeliver = only("dispatch.redeliver", to="w2", **{"from": "w1"})
    ok = only("client.ok")
    release = only("fp.release", proc="w1", hit_seq=hold.seq, barrier="A")
    if redeliver.data.get("held") != [{"proc": "w1", "hit_seq": hold.seq, "barrier": "A"}]:
        found.append(f"the takeover names {redeliver.data.get('held')}, not w1's hold")
    if not send.seq < redeliver.seq < ok.seq < release.seq:
        found.append("the orchestrator did not send, redeliver, answer, then release, in order")
    if ok.data.get("worker") != "w2":
        found.append(f"turn-1 was answered by {ok.data.get('worker')}, not w2")
    if not any(e.seq == ok.data.get("worker_seq") for e in b):
        found.append("client.ok names no event of w2")
    reads = [e for e in b if (e.kind, e.op_id) == ("saver.get_tuple.return", op)]
    puts = [e for e in b if (e.kind, e.op_id) == ("saver.put.call", op)]
    if not reads or reads[0].checkpoint_id != hold.parent_checkpoint_id:
        found.append("w2 did not read the checkpoint w1's held put extends")
    if not puts or puts[0].parent_checkpoint_id != hold.parent_checkpoint_id:
        found.append("w2's first put does not extend that checkpoint")
    return found


def fork_rows(directory: Path) -> dict[str, Any]:
    """The checkpoint rows after quiescence that share the parent of w1's held put: the fork."""
    hold = next(
        e
        for e in read_history(directory / "w1.jsonl").events
        if (e.kind, e.fp) == ("fp.hit", "put.before")
    )
    rows = json.loads((directory / "snapshot.json").read_text())["second"]["checkpoints"]
    children = sorted(
        r["checkpoint_id"] for r in rows if r["parent_checkpoint_id"] == hold.parent_checkpoint_id
    )
    head = max(r["checkpoint_id"] for r in rows)
    return {
        "parent": hold.parent_checkpoint_id,
        "stale": hold.checkpoint_id,
        "children": children,
        "head": head,
    }


Check = Callable[[TrialFiles], None]


@pytest.mark.parametrize(("name", "cell"), [("c3-w1", "C3-w1-sync"), ("c3-w5", "C3-w5-sync")])
def test_the_placebo_changes_nothing_and_every_inert_arm_is_hit(
    tmp_path: Path, name: str, cell: str
) -> None:
    summary, trials = judged(tmp_path, name)
    scenario = load_scenario(SHAPES / f"t11-{name}.yaml")
    for trial in trials:
        assert as_predicted(cell, trial) == []
        record: dict[str, Any] = json.loads((trial.directory / "trial.json").read_text())
        [toxic] = record["toxics"]
        assert (toxic["installed"], toxic["removed"]) == (True, True)
        assert (toxic["type"], toxic["attributes"]) == ("latency", {"jitter": 0, "latency": 0})
        hits = [e for e in read_history(trial.directory / "w1.jsonl").events if e.kind == "fp.hit"]
        assert len(hits) == len(scenario.schedule)
        assert {str(e.data["action"]) for e in hits} == {"none"}
        snapshot = json.loads((trial.directory / "snapshot.json").read_text())
        assert snapshot["first"]["meta"]["toxics_installed"] == []
    run = json.loads((summary.directory / "run.json").read_text())
    assert "saver_mutant" not in run


def test_k1a_shape_replicates_8039_as_preregistered(tmp_path: Path) -> None:
    _, trials = judged(tmp_path, "k1a-shape", "replication", "langgraph#8039")
    for trial in trials:
        assert as_predicted("K1a", trial) == []
        events = read_history(trial.directory / "orchestrator.jsonl").events
        [redeliver] = [e for e in events if e.kind == "dispatch.redeliver"]
        assert (redeliver.op_id, redeliver.data["to"]) == ("resume-1", "w2")
        exits = [e for e in events if e.kind == "worker.exit" and e.data["proc"] == "w1"]
        assert exits and exits[0].seq < redeliver.seq and exits[0].data["signal"] == "SIGKILL"
        terminals = [e for e in events if e.kind.startswith("client.") and e.op_id == "resume-1"]
        assert [(e.kind, e.data.get("worker")) for e in terminals] == [
            ("client.invoke", "w1"),
            ("client.ok", "w2"),
        ]


def test_a_batch_is_classified_once_and_identically_again(tmp_path: Path) -> None:
    """`crashcourt experiment classify` on K1a's shape: every trial as the draft predicts K1a, the
    checker run again on the same histories byte-identical, and a verdict never replaced."""
    path = SHAPES / "t11-k1a-shape.yaml"
    summary = run_batch(
        load_scenario(path),
        RunOptions(trials=TRIALS, out=tmp_path),
        Settings.from_environment(REPO),
        source=path,
    )
    host = json.loads((summary.directory / "run.json").read_text())["host"]
    assert all(isinstance(host[k], int) and host[k] > 0 for k in ("cpu_count", "memory_gib"))
    first = classify_batch(summary.directory, REPO)
    assert [c.invalid for c in first] == [None] * TRIALS
    assert [as_predicted("K1a", t) for t in read_trials(summary.directory)] == [[]] * TRIALS
    again = classify_batch(summary.directory, REPO, compare=True)
    assert [c.differs for c in again] == [()] * TRIALS
    with pytest.raises(NotClassified, match="already classified"):
        classify_batch(summary.directory, REPO)


def test_k4_shape_replicates_8764_as_preregistered(tmp_path: Path) -> None:
    """A kill at L-IN before the first put commits: no row, client.info, no invariant exercised,
    and the checker's record of an accepted run with no durable record."""
    _, trials = judged(tmp_path, "k4-shape", "replication", "langgraph#8764")
    for trial in trials:
        assert as_predicted("K4", trial) == []
        assert trial.result is None
        assert trial.checker["not_exercised"]  # every invariant, listed with why
        events = read_history(trial.directory / "orchestrator.jsonl").events
        terminals = [
            e.kind for e in events if e.kind.startswith("client.") and e.op_id == "start-1"
        ]
        assert terminals == ["client.invoke", "client.info"]
        snapshot = json.loads((trial.directory / "snapshot.json").read_text())
        assert snapshot["second"]["checkpoints"] == []


def test_k3_shape_replicates_8796_as_preregistered(tmp_path: Path) -> None:
    _, trials = judged(tmp_path, "k3-shape", "replication", "langgraph#8796")
    for trial in trials:
        assert as_predicted("K3", trial) == []
        record: dict[str, Any] = json.loads((trial.directory / "trial.json").read_text())
        assert record["ops"]["turn-1"]["redelivered_to"] == "w2"
        assert record["ops"]["turn-1"]["stale"] == ["w1 answered ok"]
        events = read_history(trial.directory / "orchestrator.jsonl").events
        order = [
            e.kind for e in events if e.kind in {"dispatch.redeliver", "client.ok", "fp.release"}
        ]
        assert order == ["dispatch.redeliver", "client.ok", "fp.release"]
        assert held_takeover(trial.directory) == []
        # The stale write is durable, and it and w2's first write are two children of one parent.
        fork = fork_rows(trial.directory)
        assert fork["stale"] in fork["children"] and len(fork["children"]) == 2
