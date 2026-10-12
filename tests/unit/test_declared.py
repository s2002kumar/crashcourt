"""The checker's facts for a trial, built from the batch's records, and every refusal."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from crashcourt.checker.artifacts import ArmSpec, FaultSpec
from crashcourt.checker.landmarks import LANDMARKS_ROOT, load_table
from crashcourt.orchestrator import declared
from crashcourt.orchestrator.declared import NotDeclared, trial_scenario
from crashcourt.orchestrator.dispatcher import kill_fault_id
from crashcourt.spec.content import canonical_json
from crashcourt.spec.scenario import ScenarioError, parse_scenario

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "research" / "evidence" / "t-08" / "manifest-local.json"
SOURCE = "a" * 64
ORACLE = "host=127.0.0.1 port=5432 user=crashcourt dbname=crashcourt"

W1: dict[str, Any] = {
    "v": 1,
    "name": "w1-approve",
    "cell": "w1-approve",
    "seed": "w1-approve-v1",
    "workload": "w1",
    "target": {"saver": "postgres", "driver": "async", "durability": "sync"},
    "workers": ["w1"],
    "ops": [
        {"id": "start-1", "worker": "w1", "thread": "t1", "after": "start"},
        {"id": "resume-1", "worker": "w1", "thread": "t1", "after": "start-1", "resume": "approve"},
    ],
}


def scenario(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(W1)
    data.update(changes)
    return data


def batch(
    tmp_path: Path, data: dict[str, Any], *, root: Path = LANDMARKS_ROOT, **run_changes: Any
) -> Path:
    """A batch directory as the runner writes it, with only what the builder reads."""
    directory = tmp_path / "b-1"
    directory.mkdir(parents=True)
    parsed = parse_scenario(data, landmarks_root=root)
    (directory / "scenario.json").write_text(canonical_json(parsed) + "\n", encoding="utf-8")
    (directory / "manifest.json").write_bytes(MANIFEST.read_bytes())
    run: dict[str, Any] = {
        "batch": "b-1",
        "cell": parsed.cell,
        "trials": 3,
        "oracle_dsn": ORACLE,
        "sink": {"expected_source_sha256": SOURCE},
        "routes": {
            w: {"proxy": f"postgres-{w}", "port": 15433 + i} for i, w in enumerate(parsed.workers)
        },
    }
    run.update(run_changes)
    (directory / "run.json").write_text(json.dumps(run), encoding="utf-8")
    return directory


def test_a_w1_trial_declares_its_ops_threads_target_and_provenance(tmp_path: Path) -> None:
    facts = trial_scenario(batch(tmp_path, scenario()), 2, evidence="control")
    assert (facts.batch, facts.cell, facts.trial) == ("b-1", "w1-approve", 2)
    assert (facts.provenance, facts.evidence) == ("run", "control")
    assert [(o.op_id, o.kind, o.resume, o.logical_op) for o in facts.ops] == [
        ("start-1", "start", None, "start-1"),
        ("resume-1", "resume", "approve", "resume-1"),
    ]
    assert {o.thread_id for o in facts.ops} == {"b-1/w1-approve/2/t1"}
    assert facts.threads == ("b-1/w1-approve/2/t1",)
    assert (facts.workload, facts.durability, facts.target) == ("w1", "sync", "AsyncPostgresSaver")
    assert facts.manifest_sha256 == hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert facts.runner == "local"
    assert facts.oracle_dsn == ORACLE
    assert facts.sink_source_sha256 == SOURCE
    assert facts.proxies == {"postgres-w1": "w1"}
    assert (facts.arms, facts.faults, facts.landmarks) == ((), (), None)


def test_w5_ops_are_turns(tmp_path: Path) -> None:
    ops = [
        {"id": "turn-1", "worker": "w1", "thread": "t1", "after": "start"},
        {"id": "turn-2", "worker": "w1", "thread": "t1", "after": "turn-1"},
    ]
    facts = trial_scenario(batch(tmp_path, scenario(workload="w5", ops=ops)), 1, evidence="control")
    assert [o.kind for o in facts.ops] == ["turn", "turn"]


def test_a_w5_resume_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NotDeclared, match="W5 sends turns"):
        trial_scenario(batch(tmp_path, scenario(workload="w5")), 1, evidence="control")


def test_a_workload_without_an_effect_model_is_refused(tmp_path: Path) -> None:
    ops = [{"id": "op-1", "worker": "w1", "thread": "t1", "after": "start"}]
    with pytest.raises(NotDeclared, match="no effect model for workload smoke"):
        trial_scenario(batch(tmp_path, scenario(workload="smoke", ops=ops)), 1, evidence="control")


def test_a_resume_value_that_is_not_a_string_is_refused(tmp_path: Path) -> None:
    data = scenario()
    data["ops"][1]["resume"] = {"approved": True}
    with pytest.raises(NotDeclared, match="is not a string"):
        trial_scenario(batch(tmp_path, data), 1, evidence="control")


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"trials": 1}, "trial 2 is not one of the batch's 1 trials"),
        ({"cell": "other"}, "run.json names batch"),
        ({"oracle_dsn": None}, "does not record the direct DSN"),
        ({"sink": {}}, "does not record the sink source"),
        ({"routes": {}}, "does not record the worker routes"),
        ({"routes": {"w1": {"port": 15433}}}, "names no proxy"),
    ],
)
def test_records_that_do_not_say_enough_are_refused(
    tmp_path: Path, change: dict[str, Any], problem: str
) -> None:
    with pytest.raises(NotDeclared, match=problem):
        trial_scenario(batch(tmp_path, scenario(), **change), 2, evidence="control")


def pause(point: str, occurrence: int, landmark: str, barrier: str = "B1") -> dict[str, Any]:
    on = {"fp": point, "worker": "w1", "op": "resume-1", "occurrence": occurrence}
    return {"on": on, "do": {"pause": barrier}, "landmark": landmark}


def test_a_released_pause_is_a_pause_fault_at_its_landmark(tmp_path: Path) -> None:
    schedule = [
        pause("put_writes.before", 2, "L-RES"),
        {"on": {"all_paused": ["B1"]}, "do": {"release": ["B1"]}},
    ]
    data = scenario(schedule=schedule, landmarks="langgraph-v1")
    facts = trial_scenario(batch(tmp_path, data), 1, evidence="control")
    assert facts.arms == (
        ArmSpec(
            point="put_writes.before",
            action="pause",
            worker="w1",
            op_id="resume-1",
            occurrence=2,
            landmark="L-RES",
        ),
    )
    assert facts.faults == (FaultSpec(kind="pause", arm=0),)
    assert facts.landmarks == load_table(LANDMARKS_ROOT / "langgraph-v1.yaml")


def test_a_pause_killed_while_held_is_an_arm_and_the_kill_a_sigkill(tmp_path: Path) -> None:
    schedule = [
        pause("put.before", 3, "L-T1"),
        {"on": {"all_paused": ["B1"]}, "do": {"kill": "w1"}},
    ]
    data = scenario(schedule=schedule, landmarks="langgraph-v1")
    facts = trial_scenario(batch(tmp_path, data), 1, evidence="control")
    assert [(a.action, a.landmark) for a in facts.arms] == [("pause", "L-T1")]
    assert facts.faults == (FaultSpec(kind="sigkill", fault_id=kill_fault_id(1), worker="w1"),)
    assert kill_fault_id(1) == "step-1"


def test_exit_and_sigkill_arms_are_their_faults(tmp_path: Path) -> None:
    on = {"fp": "get_tuple.after", "worker": "w1", "op": "resume-1"}
    exit_step = {"on": on, "do": {"exit": 3}, "landmark": "L-GET"}
    exits = scenario(schedule=[exit_step], landmarks="langgraph-v1")
    facts = trial_scenario(batch(tmp_path, exits), 1, evidence="control")
    assert facts.arms[0].exit_code == 3 and facts.arms[0].landmark == "L-GET"
    assert facts.faults == (FaultSpec(kind="exit", arm=0, exit_code=3),)

    kill_step = {"on": on, "do": {"signal": "SIGKILL"}, "landmark": "L-GET"}
    kills = scenario(schedule=[kill_step], landmarks="langgraph-v1")
    facts = trial_scenario(batch(tmp_path / "k", kills), 1, evidence="control")
    assert [(a.action, a.signal, a.landmark) for a in facts.arms] == [
        ("signal", "SIGKILL", "L-GET")
    ]
    assert facts.faults == (FaultSpec(kind="sigkill", arm=0),)


def test_a_signal_with_no_fault_kind_is_refused(tmp_path: Path) -> None:
    on = {"fp": "get_tuple.after", "worker": "w1", "op": "resume-1"}
    step = {"on": on, "do": {"signal": "SIGQUIT"}, "landmark": "L-GET"}
    data = scenario(schedule=[step], landmarks="langgraph-v1")
    with pytest.raises(NotDeclared, match="no fault kind for a SIGQUIT arm"):
        trial_scenario(batch(tmp_path, data), 1, evidence="control")


def test_a_batch_record_whose_step_names_a_wrong_landmark_is_refused(tmp_path: Path) -> None:
    schedule = [
        pause("put_writes.before", 2, "L-RES"),
        {"on": {"all_paused": ["B1"]}, "do": {"release": ["B1"]}},
    ]
    directory = batch(tmp_path, scenario(schedule=schedule, landmarks="langgraph-v1"))
    record = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    record["schedule"][0]["landmark"] = "L-EXW"  # put_writes, but occurrence 3
    (directory / "scenario.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ScenarioError, match="landmark L-EXW is put_writes occurrence 3"):
        trial_scenario(directory, 1, evidence="control")
    del record["schedule"][0]["landmark"]
    (directory / "scenario.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ScenarioError, match="landmark: Field required"):
        trial_scenario(directory, 1, evidence="control")


def test_the_arm_carries_the_landmark_its_step_names(tmp_path: Path) -> None:
    """Two landmarks at one method and occurrence: the step's name decides, nothing infers it."""
    table: dict[str, Any] = yaml.safe_load(
        (LANDMARKS_ROOT / "langgraph-v1.yaml").read_text(encoding="utf-8")
    )
    twin = next(s for s in table["landmarks"] if s["id"] == "L-RES")
    table["landmarks"].append({**twin, "id": "L-RES-TWIN"})
    table["name"] = "twins"
    root = tmp_path / "landmarks"
    root.mkdir()
    (root / "twins.yaml").write_text(yaml.safe_dump(table), encoding="utf-8")
    schedule = [
        pause("put_writes.before", 2, "L-RES-TWIN"),
        {"on": {"all_paused": ["B1"]}, "do": {"release": ["B1"]}},
    ]
    data = scenario(schedule=schedule, landmarks="twins")
    directory = batch(tmp_path, data, root=root)
    facts = trial_scenario(directory, 1, evidence="control", landmarks_root=root)
    assert [a.landmark for a in facts.arms] == ["L-RES-TWIN"]
    # The builder holds the step to the table again rather than trusting the loader.
    step = parse_scenario(data, landmarks_root=root).failpoint_steps()[0][1]
    real = load_table(LANDMARKS_ROOT / "langgraph-v1.yaml")
    with pytest.raises(NotDeclared, match="names landmark L-RES-TWIN, which table langgraph-v1"):
        declared._landmark(step, real)  # pyright: ignore[reportPrivateUsage]


def test_a_table_that_is_not_there_is_refused(tmp_path: Path) -> None:
    data = scenario(landmarks="no-such-table")
    with pytest.raises(NotDeclared, match="landmark table no-such-table is not in"):
        trial_scenario(batch(tmp_path, data), 1, evidence="control")
