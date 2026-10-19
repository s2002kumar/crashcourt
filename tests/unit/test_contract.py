"""The mitigation contract on synthetic trial directories of N8's shape: the four variants of
the study (nothing, fence, idempotent sink, both), a single writer, and trials it cannot read.
The fixtures follow the event order of the archived N8 batch (run 39061196254): the stale run's
two holds, the takeover, the replacement's completion, the releases, and the stale run's writes
after them. Nothing here is evidence; the ids are fixtures with uuid6's order."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.mitigation.contract import (
    CONTRACT,
    CONTRACT_FILE,
    SUMMARY_FILE,
    NotWritten,
    evaluate_batch,
    evaluate_trial,
    summarize,
    wilson,
    write_records,
)

BATCH, CELL = "DEMO-mitigation", "DEMO-N8"
THREAD = f"{BATCH}/{CELL}/1/t1"
LOGICAL = f"charge:{THREAD}"
#: Checkpoint ids in the order uuid6 would give them: P before the fork; A1 (the stale run's
#: held put) before B's; A2 generated after B finished, so it is the largest.
P, A1, B1, B2, A2 = "0001-p", "0002-a1", "0003-b1", "0004-b2", "0005-a2"
T_EXEC, T_REC_A, T_REC_B = "task-exec", "task-rec-a", "task-rec-b"
FENCE = "RaiseException: crashcourt fence: stale epoch 2 for thread t1 (owner epoch 3, {})"


def _encode(value: Any) -> tuple[str, str]:
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    type_, blob = JsonPlusSerializer().dumps_typed(value)
    return type_, base64.b64encode(blob).decode("ascii")


def _checkpoint(cid: str, parent: str | None, executed_version: str | None) -> dict[str, Any]:
    versions = {"executed": executed_version} if executed_version else {}
    return {
        "thread_id": THREAD,
        "checkpoint_ns": "",
        "checkpoint_id": cid,
        "parent_checkpoint_id": parent,
        "checkpoint": {"channel_versions": versions, "channel_values": {}},
        "metadata": {},
        "xmin": "1",
        "commit_ns": None,
    }


def _blob(version: str, executed: list[list[Any]]) -> dict[str, Any]:
    type_, blob = _encode(executed)
    return {
        "thread_id": THREAD,
        "checkpoint_ns": "",
        "channel": "executed",
        "version": version,
        "type": type_,
        "blob": blob,
        "xmin": "1",
        "commit_ns": None,
    }


def _write(cid: str, task: str) -> dict[str, Any]:
    return {
        "thread_id": THREAD,
        "checkpoint_ns": "",
        "checkpoint_id": cid,
        "task_id": task,
        "task_path": "~",
        "idx": 0,
        "channel": "charged",
        "type": "msgpack",
        "blob": "",
        "xmin": "1",
    }


def build(
    directory: Path,
    *,
    fence: str | None = None,
    idempotent: bool = False,
    redelivered: bool = True,
    result: bool = True,
) -> Path:
    """One N8-shaped trial directory. With a fence in `enforce`, the stale run's writes raise
    the fence's error and leave no row; otherwise they land and fork the thread. With the
    idempotent sink, the replacement's charge is a duplicate row."""
    directory.mkdir(parents=True)
    run = RunIdentity(BATCH, CELL, 1, "seed")
    rejected = fence == "enforce"
    epoch = {"epoch": 2} if fence else {}
    with HistoryWriter(directory / "orchestrator.jsonl", run=run, proc="orchestrator") as o:
        o.emit("worker.start", data={"proc": "w1", "pid": 11})
        o.emit("worker.start", data={"proc": "w2", "pid": 12})
        o.emit("client.invoke", op_id="resume-1", thread_id=THREAD, data={"worker": "w1"})
        o.emit("dispatch.send", op_id="resume-1", thread_id=THREAD, data={"to": "w1", **epoch})
        if redelivered:
            o.emit(
                "dispatch.redeliver",
                op_id="resume-1",
                thread_id=THREAD,
                data={
                    "to": "w2",
                    "from": "w1",
                    "step": 2,
                    "held": [
                        {"proc": "w1", "hit_seq": 5, "barrier": "A-put"},
                        {"proc": "w1", "hit_seq": 3, "barrier": "A-writes"},
                    ],
                    **({"epoch": 3} if fence else {}),
                },
            )
            o.emit(
                "client.ok",
                op_id="resume-1",
                thread_id=THREAD,
                status="ok",
                data={"worker": "w2", "worker_seq": 13},
            )
            o.emit("fp.release", data={"proc": "w1", "hit_seq": 3, "barrier": "A-writes"})
            o.emit("fp.release", data={"proc": "w1", "hit_seq": 5, "barrier": "A-put"})
        else:
            o.emit(
                "client.ok",
                op_id="resume-1",
                thread_id=THREAD,
                status="ok",
                data={"worker": "w1", "worker_seq": 13},
            )
        o.emit("worker.exit", data={"proc": "w2"})
        o.emit("worker.exit", data={"proc": "w1"})

    def run_events(w: HistoryWriter, attempt: int, *, stale: bool) -> None:
        fields: dict[str, Any] = {"op_id": "resume-1", "thread_id": THREAD}
        w.emit("node.start", **fields, checkpoint_id=P, task_id=T_EXEC, attempt=attempt)
        w.emit("effect.request", **fields, logical_op=LOGICAL, effect_key=LOGICAL, attempt=attempt)
        w.emit(
            "effect.response",
            **fields,
            logical_op=LOGICAL,
            effect_key=LOGICAL,
            attempt=attempt,
            status="ok",
            data={"sink_seq": attempt},
        )
        w.emit("saver.put_writes.call", **fields, checkpoint_id=P, task_id=T_EXEC)
        own = A1 if stale else B1
        if stale:
            w.emit("fp.hit", **fields, fp="put_writes.before", checkpoint_id=P, task_id=T_EXEC)
            w.emit("saver.put.call", **fields, checkpoint_id=own)
            w.emit("fp.hit", **fields, fp="put.before", checkpoint_id=own)
            w.emit("fp.action", **fields, fp="put_writes.before", data={"hit_seq": 3})
            w.emit("fp.action", **fields, fp="put.before", data={"hit_seq": 5})
            if rejected:
                w.emit(
                    "saver.put.raise",
                    **fields,
                    checkpoint_id=own,
                    status="fail",
                    error=FENCE.format("checkpoints"),
                )
                w.emit(
                    "saver.put_writes.raise",
                    **fields,
                    checkpoint_id=P,
                    task_id=T_EXEC,
                    status="fail",
                    error=FENCE.format("checkpoint_writes"),
                )
                return
        else:
            w.emit("saver.put.call", **fields, checkpoint_id=own)
        w.emit("saver.put.return", **fields, checkpoint_id=own, status="ok")
        w.emit("saver.put_writes.return", **fields, checkpoint_id=P, task_id=T_EXEC, status="ok")
        task, last = (T_REC_A, A2) if stale else (T_REC_B, B2)
        w.emit("node.start", **fields, checkpoint_id=own, task_id=task, attempt=attempt)
        w.emit("saver.put_writes.call", **fields, checkpoint_id=own, task_id=task)
        w.emit("saver.put.call", **fields, checkpoint_id=last)
        w.emit("saver.put.return", **fields, checkpoint_id=last, status="ok")
        w.emit("saver.put_writes.return", **fields, checkpoint_id=own, task_id=task, status="ok")

    with HistoryWriter(directory / "w1.jsonl", run=run, proc="w1") as w1:
        run_events(w1, 1, stale=redelivered)
    with HistoryWriter(directory / "w2.jsonl", run=run, proc="w2") as w2:
        if redelivered:
            run_events(w2, 2, stale=False)

    checkpoints = [_checkpoint(P, None, None)]
    writes = [_write(P, T_EXEC)]
    blobs: list[dict[str, Any]] = []
    if redelivered:
        checkpoints += [_checkpoint(B1, P, None), _checkpoint(B2, B1, "vb")]
        writes.append(_write(B1, T_REC_B))
        blobs.append(_blob("vb", [[LOGICAL, 2]]))
        if not rejected:
            checkpoints += [_checkpoint(A1, P, None), _checkpoint(A2, A1, "va")]
            writes.append(_write(A1, T_REC_A))
            blobs.append(_blob("va", [[LOGICAL, 1]]))
    else:  # the single writer's run took the stale=False path, so its ids are B's
        checkpoints += [_checkpoint(B1, P, None), _checkpoint(B2, B1, "vb")]
        writes.append(_write(B1, T_REC_B))
        blobs.append(_blob("vb", [[LOGICAL, 1]]))
    snapshot = {
        "first": None,
        "second": {"meta": {}, "checkpoints": checkpoints, "blobs": blobs, "writes": writes},
    }
    (directory / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")

    def row(seq: int, attempt: int, worker: str, status: str) -> dict[str, Any]:
        return {
            "sink_seq": seq,
            "effect_key": LOGICAL,
            "logical_op": LOGICAL,
            "op_id": "resume-1",
            "attempt": attempt,
            "worker": worker,
            "status": status,
        }

    rows = [row(1, 1, "w1", "applied")]
    if redelivered:
        rows.append(row(2, 2, "w2", "duplicate" if idempotent else "applied"))
    (directory / "ledger.json").write_text(
        json.dumps({"rows": rows, "verify_ok": True}), encoding="utf-8"
    )
    ops = {
        "resume-1": {
            "thread_id": THREAD,
            "worker": "w1",
            "redelivered_to": "w2" if redelivered else None,
        }
    }
    trial = {"trial": 1, "ops": ops, "workers": {"w1": {}, "w2": {}}}
    (directory / "trial.json").write_text(json.dumps(trial), encoding="utf-8")
    if result:
        outcomes = [{"invariant": "I4", "outcome": "GAP"}, {"invariant": "I5a", "outcome": "GAP"}]
        (directory / "result.json").write_text(json.dumps({"results": outcomes}), encoding="utf-8")
    if fence:
        owners = {"v": 1, "mode": fence, "owners": {THREAD: 3 if redelivered else 2}}
        (directory / "fence.json").write_text(json.dumps(owners), encoding="utf-8")
    return directory


def test_the_baseline_shape_reads_as_the_archived_n8_batch_did(tmp_path: Path) -> None:
    record = evaluate_trial(build(tmp_path / "t"), fence=None, idempotent_effects=False)
    assert record["problems"] == [] and record["contract"] == CONTRACT
    assert record["acknowledged"] == {"worker": "w2", "op": "resume-1", "worker_seq": 13}
    checkpoints = record["checkpoints"]
    assert checkpoints["fork"] and checkpoints["forks"] == {P: [A1, B1]}
    assert checkpoints["head"] == A2 and checkpoints["head_writer"] == ["w1", "resume-1"]
    assert checkpoints["head_attempts"] == [1] and not checkpoints["acknowledged_is_head"]
    stale = record["stale"]
    assert stale["resumed"] and stale["attempted"] == {"checkpoints": 2, "writes": 2}
    # The held execute write hit ON CONFLICT DO NOTHING behind w2's; the record write landed.
    assert stale["durable"] == {"checkpoints": 2, "writes": 1}
    assert stale["fenced"] == {"checkpoints": 0, "writes": 0} and stale["other_errors"] == 0
    assert not stale["every_attempt_fenced"] and stale["acknowledged_not_durable"] == []
    [effect] = record["effects"]
    assert (effect["requested"], effect["applied"], effect["duplicates"]) == (2, 2, 0)
    assert effect["logical"] == "DUPLICATED" and record["logical"] == "DUPLICATED"
    assert effect["attempt_consistency"] == "NOT_APPLICABLE"
    assert record["fence"] is None and record["v1"] == {"I4": "GAP", "I5a": "GAP"}


def test_the_fence_keeps_the_acknowledged_branch_as_the_head_and_every_attempt_fenced(
    tmp_path: Path,
) -> None:
    record = evaluate_trial(
        build(tmp_path / "t", fence="enforce"), fence="enforce", idempotent_effects=False
    )
    assert record["problems"] == []
    checkpoints = record["checkpoints"]
    assert not checkpoints["fork"] and checkpoints["head"] == B2
    assert checkpoints["head_writer"] == ["w2", "resume-1"] and checkpoints["head_attempts"] == [2]
    assert checkpoints["acknowledged_is_head"]
    stale = record["stale"]
    assert stale["attempted"] == {"checkpoints": 1, "writes": 1}
    assert stale["durable"] == {"checkpoints": 0, "writes": 0}
    assert stale["fenced"] == {"checkpoints": 1, "writes": 1} and stale["every_attempt_fenced"]
    assert record["logical"] == "DUPLICATED"
    assert record["fence"] == {
        "mode": "enforce",
        "owner_epoch": 3,
        "acknowledged_epoch": 3,
        "consistent": True,
    }


def test_the_idempotent_sink_applies_once_but_the_stale_branch_still_becomes_the_head(
    tmp_path: Path,
) -> None:
    record = evaluate_trial(
        build(tmp_path / "t", idempotent=True), fence=None, idempotent_effects=True
    )
    assert record["problems"] == []
    assert record["checkpoints"]["fork"] and not record["checkpoints"]["acknowledged_is_head"]
    [effect] = record["effects"]
    assert (effect["applied"], effect["duplicates"]) == (1, 1)
    assert effect["attempts_applied"] == [1] and effect["attempts_duplicate"] == [2]
    assert record["logical"] == "EXACTLY_ONCE"
    # The head is the stale branch, whose state names attempt 1: the applied one.
    assert effect["attempt_consistency"] == "CONSISTENT"


def test_both_mitigations_leave_the_residual_the_study_predicts(tmp_path: Path) -> None:
    record = evaluate_trial(
        build(tmp_path / "t", fence="enforce", idempotent=True),
        fence="enforce",
        idempotent_effects=True,
    )
    assert record["problems"] == []
    assert not record["checkpoints"]["fork"] and record["checkpoints"]["acknowledged_is_head"]
    assert record["stale"]["every_attempt_fenced"]
    [effect] = record["effects"]
    assert record["logical"] == "EXACTLY_ONCE" and effect["attempts_applied"] == [1]
    # The selected head names attempt 2 while the applied charge is attempt 1.
    assert record["checkpoints"]["head_attempts"] == [2]
    assert effect["attempt_consistency"] == "INCONSISTENT"


def test_the_placebo_reads_as_the_baseline_with_its_owner_rows(tmp_path: Path) -> None:
    record = evaluate_trial(
        build(tmp_path / "t", fence="placebo"), fence="placebo", idempotent_effects=False
    )
    assert record["problems"] == []
    assert record["checkpoints"]["fork"] and record["stale"]["durable"] == {
        "checkpoints": 2,
        "writes": 1,
    }
    assert record["stale"]["fenced"] == {"checkpoints": 0, "writes": 0}
    assert record["fence"]["mode"] == "placebo" and record["fence"]["consistent"]


def test_a_single_writer_has_no_stale_run_and_one_applied_charge(tmp_path: Path) -> None:
    record = evaluate_trial(
        build(tmp_path / "t", redelivered=False), fence=None, idempotent_effects=False
    )
    assert record["problems"] == [] and record["stale"] is None
    assert record["acknowledged"]["worker"] == "w1"
    assert not record["checkpoints"]["fork"] and record["checkpoints"]["acknowledged_is_head"]
    [effect] = record["effects"]
    assert (effect["requested"], effect["applied"]) == (1, 1)
    assert record["logical"] == "EXACTLY_ONCE" and effect["attempt_consistency"] == "CONSISTENT"


def test_an_unreadable_trial_records_its_problems_and_no_measurements(tmp_path: Path) -> None:
    directory = build(tmp_path / "t", result=False)
    (directory / "ledger.json").write_text(json.dumps({"rows": [], "verify_ok": False}))
    record = evaluate_trial(directory, fence=None, idempotent_effects=False)
    assert record["problems"] == ["ledger.json is not an export the sink verified"]
    assert "effects" not in record and "logical" not in record
    (directory / "snapshot.json").unlink()
    record = evaluate_trial(directory, fence=None, idempotent_effects=False)
    assert record["problems"] and record["problems"][0].startswith("FileNotFoundError")


def batch(tmp_path: Path, **variant: Any) -> Path:
    root = tmp_path / "batch"
    root.mkdir(parents=True)
    scenario = {"cell": CELL, **({"variant": variant} if variant else {})}
    (root / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    (root / "batch.json").write_text(
        json.dumps({"batch": BATCH, "scenario_sha256": "0" * 64}), encoding="utf-8"
    )
    fence = variant.get("fence")
    for n in (1, 2, 3):
        build(
            root / CELL / f"trial-{n:05d}",
            fence=fence,
            idempotent=bool(variant.get("idempotent_effects")),
        )
    return root


def test_a_batch_is_summarized_with_counts_and_intervals_and_written_once(tmp_path: Path) -> None:
    root = batch(tmp_path, fence="enforce", idempotent_effects=True)
    records = evaluate_batch(root)
    assert [r["trial"] for r in records] == [1, 1, 1]  # the fixture's trial.json says 1
    summary = summarize(root, records)
    assert (summary["cell"], summary["trials"], summary["readable"]) == (CELL, 3, 3)
    assert summary["variant"] == {"fence": "enforce", "idempotent_effects": True}
    assert summary["fork"] == {
        "count": 0,
        "of": 3,
        "wilson95": [0.0, pytest.approx(0.5615, abs=1e-3)],
    }
    assert summary["acknowledged_is_head"]["count"] == 3
    assert summary["stale"]["every_attempt_fenced"]["count"] == 3
    assert summary["stale"]["none_durable"]["count"] == 3
    assert summary["logical"] == {"EXACTLY_ONCE": 3}
    assert summary["effects"]["attempt_consistency"] == {"INCONSISTENT": 3}
    assert summary["fence"] == {
        "trials": 3,
        "consistent": {"count": 3, "of": 3, "wilson95": [pytest.approx(0.4385, abs=1e-3), 1.0]},
    }
    assert summary["v1"] == {"I4": {"GAP": 3}, "I5a": {"GAP": 3}}

    written, again = write_records(root)
    assert written == records and again == summary
    assert (root / SUMMARY_FILE).exists()
    for directory in sorted(root.rglob(CONTRACT_FILE)):
        assert json.loads(directory.read_text(encoding="utf-8"))["contract"] == CONTRACT
    write_records(root)  # identical records are fine
    (root / SUMMARY_FILE).write_text("{}", encoding="utf-8")
    with pytest.raises(NotWritten):
        write_records(root)


def test_the_baseline_batch_summary_counts_the_fork_and_the_duplicates(tmp_path: Path) -> None:
    summary = summarize(batch(tmp_path), evaluate_batch(batch(tmp_path / "other")))
    assert summary["variant"] == {"fence": None, "idempotent_effects": False}
    assert summary["fork"]["count"] == 3 and summary["acknowledged_is_head"]["count"] == 0
    assert summary["stale"]["none_durable"]["count"] == 0
    assert summary["stale"]["every_attempt_fenced"]["count"] == 0
    assert summary["logical"] == {"DUPLICATED": 3}
    assert summary["fence"] == {"trials": 0, "consistent": {"count": 0, "of": 0, "wilson95": None}}


def test_wilson_intervals() -> None:
    assert wilson(0, 0) is None
    low, high = wilson(20, 20)  # type: ignore[misc]
    assert low == pytest.approx(0.8389, abs=1e-3) and high == 1.0
    low, high = wilson(0, 20)  # type: ignore[misc]
    assert low == 0.0 and high == pytest.approx(0.1611, abs=1e-3)


# -- the judgement against the committed predictions ----------------------------------------------


ROOT = Path(__file__).resolve().parents[2]


def committed_prediction(cell: str) -> Any:
    from crashcourt.experiment.preregistration import PREREGISTRATION_PATH, parse

    prereg = parse((ROOT / PREREGISTRATION_PATH).read_text(encoding="utf-8"))
    return prereg.cell(cell).contract


@pytest.mark.parametrize(
    ("cell", "fence", "idempotent", "redelivered"),
    [
        ("N8-base", None, False, True),
        ("N8-placebo", "placebo", False, True),
        ("N8-F", "enforce", False, True),
        ("N8-I", None, True, True),
        ("N8-FI", "enforce", True, True),
        ("C1-w1-sync-fenced", "enforce", False, False),
    ],
)
def test_each_committed_prediction_is_met_by_its_shape(
    tmp_path: Path, cell: str, fence: str | None, idempotent: bool, redelivered: bool
) -> None:
    """The pre-registered contract predictions describe exactly the shapes the study expects:
    each synthetic trial of the cell's shape is judged as predicted, and the baseline's shape
    differs from every mitigated cell's prediction."""
    from crashcourt.mitigation.judge import judge

    prediction = committed_prediction(cell)
    assert prediction is not None
    records = [
        evaluate_trial(
            build(
                tmp_path / f"t{n}",
                fence=fence,
                idempotent=idempotent,
                redelivered=redelivered,
            ),
            fence=fence,
            idempotent_effects=idempotent,
        )
        for n in (1, 2)
    ]
    verdict = judge(records, prediction, deterministic=True)
    assert verdict["status"] == "as-predicted", verdict
    assert verdict["trials_as_predicted"] == 2 and verdict["unreadable"] == 0
    assert all(m["mismatches"] == {} for m in verdict["measures"].values())
    if cell not in ("N8-base", "N8-placebo"):
        baseline = evaluate_trial(build(tmp_path / "b"), fence=None, idempotent_effects=False)
        differs = judge([baseline], prediction, deterministic=True)
        assert differs["status"] == "differs" and differs["trials_as_predicted"] == 0


def test_a_split_batch_is_unstable_and_an_unreadable_trial_never_as_predicted(
    tmp_path: Path,
) -> None:
    from crashcourt.mitigation.judge import judge

    prediction = committed_prediction("N8-base")
    good = evaluate_trial(build(tmp_path / "g"), fence=None, idempotent_effects=False)
    bad = evaluate_trial(
        build(tmp_path / "b", fence="enforce"), fence="enforce", idempotent_effects=False
    )
    split = judge([good, bad], prediction, deterministic=True)
    assert split["status"] == "unstable" and split["trials_as_predicted"] == 1
    assert split["measures"]["fork"]["mismatches"] == {"1": False}
    assert judge([good, bad], prediction, deterministic=False)["status"] == "differs"
    build(tmp_path / "u", result=False)
    (tmp_path / "u" / "ledger.json").unlink()
    unreadable = evaluate_trial(tmp_path / "u", fence=None, idempotent_effects=False)
    assert unreadable["problems"]
    verdict = judge([good, unreadable], prediction, deterministic=True)
    assert verdict["status"] == "differs" and verdict["unreadable"] == 1
    assert judge([], prediction, deterministic=True)["status"] == "missing"


def test_a_batch_summary_carries_the_judgement_for_an_authorized_cell(tmp_path: Path) -> None:
    """With a repository, the summary judges the batch against the cell's committed prediction,
    read from the pre-registration at the authorized commit; the CLI prints it."""
    from crashcourt.cli import main
    from crashcourt.experiment.gate import AUTHORIZATION_FILE, Authorization
    from crashcourt.experiment.preregistration import PREREGISTRATION_PATH

    from ._experiment import commit, git

    # A repository whose pre-registration commit holds this checkout's pre-registration.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    prereg_text = (ROOT / PREREGISTRATION_PATH).read_text(encoding="utf-8")
    head = commit(repo, {str(PREREGISTRATION_PATH): prereg_text}, "pre-register")
    root = batch(tmp_path, fence="enforce", idempotent_effects=True)

    authorization = Authorization(
        cell="N8-FI",
        phase="new-evidence",
        evidence="new-evidence",
        scenario="scenarios/mitigation/N8-FI.yaml",
        scenario_sha256="0" * 64,
        master_seed="N8-FI-v1",
        trials=3,
        run_commit=head,
        preregistration_commit=head,
        preregistration_sha256="0" * 64,
        preregistration_state="final",
        freeze_commit=head,
        frozen_md_sha256="0" * 64,
        checker_version="v1",
        checker_sha256="0" * 64,
        registry_sha256="0" * 64,
        landmark_table_sha256=None,
    )
    (root / AUTHORIZATION_FILE).write_text(authorization.to_json(), encoding="utf-8")
    _, summary = write_records(root, repo)
    assert summary["judgement"]["status"] == "as-predicted"
    assert main(["mitigation", "contract", str(root), "--root", str(repo)]) == 0
    assert "judgement" in json.loads((root / SUMMARY_FILE).read_text(encoding="utf-8"))
