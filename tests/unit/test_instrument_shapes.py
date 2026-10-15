"""The scenario keys the instrument cells need (action none, whole-trial placebo toxics, and
redelivery), what loading refuses, and how the checker's facts declare them.

The shapes are C3's (placebo), K1a's (langgraph#8039: a kill, then the replacement takes the op
over), and K3's (langgraph#8796, held: a stale worker held while another takes the op over).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from crashcourt.checker.artifacts import ArmSpec, FaultSpec
from crashcourt.orchestrator.declared import trial_scenario
from crashcourt.spec.scenario import (
    ScenarioError,
    load_scenario,
    parse_scenario,
    read_yaml,
    scenario_sha256,
)

from .test_declared import batch

ROOT = Path(__file__).resolve().parents[2]
SHAPES = ROOT / "tests" / "integration" / "scenarios"


def shape(name: str) -> dict[str, Any]:
    data = read_yaml((SHAPES / f"t11-{name}.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return copy.deepcopy(data)  # pyright: ignore[reportUnknownArgumentType]


def refused(data: dict[str, Any]) -> str:
    with pytest.raises(ScenarioError) as caught:
        parse_scenario(data)
    return str(caught.value)


def test_every_shape_loads() -> None:
    for name in ("c3-w1", "c3-w5", "k1a-shape", "k3-shape"):
        parse_scenario(shape(name))


def test_scenarios_without_the_new_keys_keep_their_hashes() -> None:
    # The scenarios T-09 recorded that arm nothing; w5-concurrent's steps gained explicit
    # landmarks in #31, which changed its hash there.
    for name in ("w1-approve", "w1-misuse", "w1-reject", "w5-sequential"):
        recorded = ROOT / "research" / "evidence" / "t-09" / f"t09-{name}"
        expected = json.loads((recorded / "batch.json").read_text(encoding="utf-8"))
        loaded = load_scenario(ROOT / "scenarios" / f"{name}.yaml")
        assert scenario_sha256(loaded) == expected["scenario_sha256"], name


def test_a_toxic_enters_the_hash() -> None:
    with_toxic = shape("c3-w1")
    without = {k: v for k, v in with_toxic.items() if k != "toxics"}
    assert scenario_sha256(parse_scenario(with_toxic)) != scenario_sha256(parse_scenario(without))


@pytest.mark.parametrize(
    ("toxic", "problem"),
    [
        ({"latency_ms": 5}, "latency_ms"),
        ({"jitter_ms": 1}, "jitter_ms"),
        ({"type": "timeout"}, "type"),
        ({"latency_ms": True}, "latency_ms"),
        ({"worker": "w2"}, "a toxic is on w2, which is not a worker"),
    ],
)
def test_only_the_placebo_toxic_is_accepted(toxic: dict[str, Any], problem: str) -> None:
    data = shape("c3-w1")
    data["toxics"] = [{**data["toxics"][0], **toxic}]
    assert problem in refused(data)


def test_one_toxic_per_worker_and_stream() -> None:
    data = shape("c3-w1")
    data["toxics"] = [data["toxics"][0], data["toxics"][0]]
    assert "two toxics on w1 downstream" in refused(data)


def test_an_inert_arm_says_so_and_does_not_end_its_worker() -> None:
    data = shape("c3-w1")
    data["schedule"][0]["do"] = {"none": False}
    assert "none" in refused(data)
    data = shape("k1a-shape")
    # An inert arm beside the killing one: the worker is still ended by one step only.
    data["schedule"].insert(
        0,
        {
            "on": {"fp": "put_writes.after", "worker": "w1", "op": "resume-1", "occurrence": 2},
            "do": {"none": True},
            "landmark": "L-RES",
        },
    )
    parse_scenario(data)


def redeliver_step(data: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in data["schedule"] if "redeliver" in s["do"])


def only_w1(d: dict[str, Any]) -> None:
    d["workers"] = ["w1"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda d: redeliver_step(d)["do"]["redeliver"].update(op="resume-9"),
            "op resume-9 is not declared",
        ),
        (
            lambda d: (only_w1(d), redeliver_step(d)["do"]["redeliver"].update(to="w1")),
            "resume-1 is sent to w1 already",
        ),
        (
            lambda d: (only_w1(d), redeliver_step(d)["do"]["redeliver"].update(to="w3")),
            "w3 is not a worker",
        ),
        (lambda d: d["schedule"].pop(0), "no step ends w1, so when it exits is up to the harness"),
        (
            lambda d: redeliver_step(d)["do"]["redeliver"].update(op="start-1"),
            "step 0 ends w1 in resume-1, not in start-1",
        ),
        (
            lambda d: d["schedule"].append(copy.deepcopy(redeliver_step(d))),
            "resume-1 is redelivered by step 1 too",
        ),
    ],
)
def test_a_replacement_takes_over_only_the_op_its_worker_was_ended_in(
    change: Any, problem: str
) -> None:
    data = shape("k1a-shape")
    change(data)
    assert problem in refused(data)


def test_a_redelivery_target_must_outlive_the_op() -> None:
    data = shape("k3-shape")
    data["ops"].append({"id": "turn-2", "worker": "w2", "thread": "t2", "after": "start"})
    data["schedule"].append(
        {
            "on": {"fp": "put.before", "worker": "w2", "op": "turn-2", "occurrence": 1},
            "do": {"exit": 3},
            "landmark": "L-IN",
        }
    )
    assert "ends w2, so it may be gone when turn-1 reaches it" in refused(data)


def test_a_takeover_must_hold_the_op_it_redelivers() -> None:
    data = shape("k3-shape")
    data["ops"].append({"id": "turn-2", "worker": "w1", "thread": "t1", "after": "turn-1"})
    data["schedule"][1]["do"]["redeliver"]["op"] = "turn-2"
    data["schedule"][2]["on"]["ended"] = "turn-2"
    problems = refused(data)
    assert "none of its barriers holds a call of turn-2 on w1" in problems


def test_a_release_after_an_op_ends_must_free_that_ops_stale_run() -> None:
    data = shape("k3-shape")
    data["schedule"][1] = {"on": {"all_paused": ["A"]}, "do": {"release": ["A"]}}
    data["schedule"][2]["do"]["release"] = ["A"]
    assert "barrier A is released by two steps" in refused(data)
    data = shape("k3-shape")
    del data["schedule"][1]
    data["workers"] = ["w1"]
    assert "turn-1 is not taken over from a held call" in refused(data)
    data = shape("k3-shape")
    data["schedule"][2]["do"]["release"] = ["B"]
    assert "barrier B is not paused by any step" in refused(data)


def test_the_replacement_counts_as_a_worker_with_work() -> None:
    data = shape("k1a-shape")
    del data["schedule"][1]
    assert "worker w2 is sent no op" in refused(data)


def facts(tmp_path: Path, name: str) -> Any:
    return trial_scenario(batch(tmp_path, shape(name)), 1, evidence="control")


def test_k1a_declares_the_kill_and_the_redelivery(tmp_path: Path) -> None:
    declared = facts(tmp_path, "k1a-shape")
    assert declared.arms == (
        ArmSpec(
            point="put_writes.before",
            action="signal",
            signal="SIGKILL",
            worker="w1",
            op_id="resume-1",
            occurrence=3,
            landmark="L-EXW",
        ),
    )
    assert declared.faults == (
        FaultSpec(kind="sigkill", arm=0),
        FaultSpec(kind="redelivery", op_ids=("resume-1",), worker="w2"),
    )


def test_k3_declares_the_released_pause_and_the_redelivery(tmp_path: Path) -> None:
    declared = facts(tmp_path, "k3-shape")
    assert [(a.action, a.landmark) for a in declared.arms] == [("pause", "L-T1")]
    assert declared.faults == (
        FaultSpec(kind="pause", arm=0),
        FaultSpec(kind="redelivery", op_ids=("turn-1",), worker="w2"),
    )


def test_c3_declares_inert_arms_at_their_landmarks_and_no_fault(tmp_path: Path) -> None:
    declared = facts(tmp_path, "c3-w1")
    assert [(a.point, a.action, a.landmark) for a in declared.arms] == [
        ("get_tuple.after", "none", "L-GET"),
        ("put_writes.before", "none", "L-RES"),
        ("put_writes.after", "none", "L-RES"),
        ("put_writes.before", "none", "L-EXW"),
        ("put_writes.after", "none", "L-EXW"),
        ("put.before", "none", "L-EXC"),
    ]
    assert declared.faults == ()
    assert [(a.action, a.landmark) for a in facts(tmp_path / "w5", "c3-w5").arms] == [
        ("none", "L-T1")
    ]
