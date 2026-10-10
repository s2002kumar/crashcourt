"""Scenarios load into frozen models, refuse what cannot run as declared, and hash by meaning."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from crashcourt.failpoints.registry import POINTS
from crashcourt.spec.scenario import (
    Scenario,
    ScenarioError,
    arm_of,
    load_scenario,
    parse_scenario,
    read_yaml,
    scenario_sha256,
    trial_seed,
)

REPO = Path(__file__).resolve().parents[2]

BASE: dict[str, Any] = {
    "v": 1,
    "name": "two-barriers",
    "cell": "pg-async-sync-smoke",
    "seed": "seed-1",
    "workload": "smoke",
    "target": {"saver": "postgres", "driver": "async", "durability": "sync"},
    "workers": ["w1", "w2"],
    "ops": [
        {"id": "op-1", "worker": "w1", "thread": "t1", "after": "start"},
        {"id": "op-2", "worker": "w2", "thread": "t2", "after": "start"},
        {"id": "op-3", "worker": "w2", "thread": "t1", "after": "op-1"},
    ],
    "schedule": [
        {"on": {"fp": "get_tuple.after", "worker": "w1", "op": "op-1"}, "do": {"pause": "B1"}},
        {"on": {"fp": "get_tuple.after", "worker": "w2", "op": "op-2"}, "do": {"pause": "B2"}},
        {"on": {"all_paused": ["B1", "B2"]}, "do": {"release": ["B1", "B2"]}},
    ],
}


def scenario(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE)
    data.update(changes)
    return data


def refused(data: dict[str, Any]) -> str:
    with pytest.raises(ScenarioError) as caught:
        parse_scenario(data)
    return str(caught.value)


def test_the_smoke_scenario_loads() -> None:
    smoke = load_scenario(REPO / "scenarios" / "smoke.yaml")
    assert smoke.workers == ("w1", "w2")
    assert [op.after for op in smoke.ops] == ["start", "start", "op-1", "op-2"]
    assert len(smoke.failpoint_steps()) == 2 and len(smoke.barrier_steps()) == 1


def test_a_valid_scenario_is_frozen() -> None:
    loaded = parse_scenario(BASE)
    with pytest.raises(ValueError, match="frozen"):
        loaded.name = "other"  # type: ignore[misc]


def test_the_on_key_stays_a_key() -> None:
    # YAML 1.1 reads `on` as True; the scenario loader reads booleans as YAML 1.2 does.
    assert read_yaml("on: {fp: put.before}\nyes: no\nflag: true") == {
        "on": {"fp": "put.before"},
        "yes": "no",
        "flag": True,
    }


def test_an_unknown_failpoint_fails_at_load() -> None:
    data = scenario()
    data["schedule"][0]["on"]["fp"] = "get_tuple.during"
    assert "get_tuple.during" not in POINTS
    assert "schedule.0" in refused(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["ops"][0].update(worker="w3"), "sent to w3, which is not a worker"),
        (lambda d: d["ops"][2].update(after="op-9"), "not an earlier op"),
        (lambda d: d["ops"][1].update(id="op-1"), "declared twice"),
        (lambda d: d["ops"][0].update(id="start"), "reserved"),
        (lambda d: d["schedule"][0]["on"].update(worker="w2"), "never reaches a failpoint"),
        (lambda d: d["schedule"][0]["on"].update(op="op-7"), "op op-7 is not declared"),
        (lambda d: d["schedule"][2]["on"].update(all_paused=["B1", "B9"]), "B9 is not paused"),
        (lambda d: d["schedule"][2]["do"].update(release=["B1", "B1"]), "released by two steps"),
        (lambda d: d["schedule"][1]["do"].update(pause="B1"), "declared twice"),
        (
            lambda d: d["schedule"].append({"on": {"all_paused": ["B1"]}, "do": {"kill": "w9"}}),
            "w9",
        ),
        (
            lambda d: d["schedule"][2].update(do={"release": ["B1", "B2", "B3"]}),
            "B3 is not paused",
        ),
    ],
)
def test_what_cannot_run_as_declared_is_refused(change: Any, message: str) -> None:
    data = scenario()
    change(data)
    assert message in refused(data)


def test_a_worker_with_no_op_is_refused() -> None:
    data = scenario(workers=["w1", "w2"], ops=[BASE["ops"][0]], schedule=[])
    assert "w2 is sent no op" in refused(data)


def test_a_third_worker_is_refused() -> None:
    assert "workers" in refused(scenario(workers=["w1", "w2", "w3"]))


def test_barriers_of_ordered_ops_can_never_be_held_together() -> None:
    data = scenario()
    data["ops"][1]["after"] = "op-1"  # op-2 now waits for op-1, which is parked at B1
    assert "never held together" in refused(data)


def test_releasing_a_barrier_the_trigger_does_not_hold_is_refused() -> None:
    data = scenario()
    data["schedule"][2] = {"on": {"all_paused": ["B1"]}, "do": {"release": ["B1", "B2"]}}
    assert "releases B2, which its trigger does not hold" in refused(data)


def test_two_steps_that_could_fire_on_one_call_are_refused() -> None:
    data = scenario()
    data["schedule"].append(
        {"on": {"fp": "get_tuple.after", "worker": "w1", "op": "op-1"}, "do": {"exit": 3}}
    )
    assert "two steps fire on op-1 occurrence 1" in refused(data)


def test_a_step_for_any_op_must_be_alone_at_its_point() -> None:
    data = scenario()
    data["schedule"].append({"on": {"fp": "get_tuple.after", "worker": "w1"}, "do": {"exit": 3}})
    assert "must be the only step at that point" in refused(data)


def test_sigstop_at_a_failpoint_is_refused_until_something_can_continue_it() -> None:
    data = scenario()
    data["schedule"][0]["do"] = {"signal": "SIGSTOP"}
    data["schedule"].pop()  # its barrier is gone too
    data["schedule"].pop()
    assert "SIGSTOP" in refused(data)


def test_unknown_keys_and_versions_are_refused() -> None:
    assert "extra" in refused(scenario(trials=50)).lower()
    assert "v" in refused(scenario(v=2))
    assert "workload" in refused(scenario(workload="w9"))


def test_every_problem_is_reported_at_once() -> None:
    data = scenario()
    data["ops"][0]["worker"] = "w3"
    data["schedule"][2]["on"]["all_paused"] = ["B1", "B9"]
    text = refused(data)
    assert "w3" in text and "B9" in text


def test_arms_carry_the_step() -> None:
    loaded = parse_scenario(
        scenario(
            ops=BASE["ops"][:2],
            schedule=[
                {
                    "on": {"fp": "put.before", "worker": "w1", "op": "op-1", "occurrence": 2},
                    "do": {"signal": "SIGKILL"},
                },
                {"on": {"fp": "put.after", "worker": "w2", "op": "op-2"}, "do": {"exit": 7}},
            ],
        )
    )
    (_, first), (_, second) = loaded.failpoint_steps()
    one, two = arm_of(first), arm_of(second)
    assert (one.point, one.action, one.op_id, one.occurrence, one.signal) == (
        "put.before",
        "signal",
        "op-1",
        2,
        "SIGKILL",
    )
    assert (two.action, two.exit_code, two.worker) == ("exit", 7, "w2")


# -- schedules whose outcome would depend on the order of hits ---------------------------------


def test_a_barrier_named_by_two_triggers_is_refused() -> None:
    # Whether step 3 can ever fire would depend on which barrier is hit first.
    data = scenario()
    data["schedule"][2] = {"on": {"all_paused": ["B1"]}, "do": {"release": ["B1"]}}
    data["schedule"].append({"on": {"all_paused": ["B1", "B2"]}, "do": {"release": ["B2"]}})
    assert "barrier B1 is in the triggers of steps 2 and 3" in refused(data)


def test_release_and_kill_on_one_trigger_is_refused() -> None:
    data = scenario(ops=BASE["ops"][:2])
    data["schedule"] = [
        BASE["schedule"][0],
        {"on": {"all_paused": ["B1"]}, "do": {"release": ["B1"]}},
        {"on": {"all_paused": ["B1"]}, "do": {"kill": "w1"}},
    ]
    assert "barrier B1 is in the triggers of steps 1 and 2" in refused(data)


KILL_W1 = [
    {"on": {"fp": "put.before", "worker": "w1", "op": "op-1"}, "do": {"pause": "B1"}},
    {"on": {"all_paused": ["B1"]}, "do": {"kill": "w1"}},
]


def test_a_kill_of_a_parked_worker_with_nothing_else_to_do_loads() -> None:
    parse_scenario(scenario(ops=BASE["ops"][:2], schedule=KILL_W1))


def test_a_kill_while_the_worker_has_other_unordered_work_is_refused() -> None:
    ops = [*BASE["ops"][:2], {"id": "op-4", "worker": "w1", "thread": "t4", "after": "op-2"}]
    text = refused(scenario(ops=ops, schedule=KILL_W1))
    assert "step 1: op op-4 on w1 is not ordered before the call that ends w1" in text


def test_work_ordered_before_the_parked_call_is_allowed() -> None:
    ops = [
        {"id": "op-0", "worker": "w1", "thread": "t0", "after": "start"},
        {"id": "op-1", "worker": "w1", "thread": "t1", "after": "op-0"},
        {"id": "op-2", "worker": "w2", "thread": "t2", "after": "start"},
    ]
    parse_scenario(scenario(ops=ops, schedule=KILL_W1))


def test_a_kill_must_hold_one_of_its_workers_calls() -> None:
    schedule = [
        {"on": {"fp": "put.before", "worker": "w2", "op": "op-2"}, "do": {"pause": "B2"}},
        {"on": {"all_paused": ["B2"]}, "do": {"kill": "w1"}},
    ]
    text = refused(scenario(ops=BASE["ops"][:2], schedule=schedule))
    assert "kills w1 without holding one of its calls by op" in text


def test_a_step_that_ends_its_worker_must_name_its_op_and_be_its_last_work() -> None:
    any_op = [{"on": {"fp": "put.before", "worker": "w1"}, "do": {"exit": 1}}]
    assert "must name its op" in refused(scenario(ops=BASE["ops"][:2], schedule=any_op))
    ends_w2 = [{"on": {"fp": "put.before", "worker": "w2", "op": "op-2"}, "do": {"exit": 1}}]
    text = refused(scenario(schedule=ends_w2))  # op-3 also runs on w2, unordered with op-2
    assert "op op-3 on w2 is not ordered before the call that ends w2" in text


def test_a_worker_is_ended_by_one_step_at_most() -> None:
    schedule = [
        *KILL_W1,
        {
            "on": {"fp": "put.before", "worker": "w1", "op": "op-1", "occurrence": 2},
            "do": {"exit": 1},
        },
    ]
    text = refused(scenario(ops=BASE["ops"][:2], schedule=schedule))
    assert "w1 is ended by steps 1 and 2; only one can act" in text


def test_every_scenario_in_the_repository_loads() -> None:
    files = [*REPO.glob("scenarios/*.yaml"), *REPO.glob("tests/integration/scenarios/*.yaml")]
    assert len(files) >= 5
    for path in files:
        load_scenario(path)


# -- the content hash -------------------------------------------------------------------------

YAML = """
v: 1
name: two-barriers
cell: pg-async-sync-smoke
seed: seed-1
workload: smoke
target: {saver: postgres, driver: async, durability: sync}
workers: [w1, w2]
ops:
  - {id: op-1, worker: w1, thread: t1, after: start}
  - {id: op-2, worker: w2, thread: t2, after: start}
  - {id: op-3, worker: w2, thread: t1, after: op-1}
schedule:
  - on: {fp: get_tuple.after, worker: w1, op: op-1}
    do: {pause: B1}
  - on: {fp: get_tuple.after, worker: w2, op: op-2}
    do: {pause: B2}
  - on: {all_paused: [B1, B2]}
    do: {release: [B1, B2]}
"""

# The same scenario: keys reordered, comments, quoting, and defaults stated.
EQUIVALENT = """
# reordered, with the defaults written out
schedule:
- do: {pause: "B1"}
  on: {worker: w1, fp: 'get_tuple.after', op: op-1, occurrence: 1}
- on: {op: op-2, fp: get_tuple.after, worker: w2}
  do: {pause: B2}
- {on: {all_paused: [B1, B2]}, do: {release: [B1, B2]}}
timeouts: {run_s: 60, hello_s: 60.0, shutdown_s: 30}
workers: [w1, w2]
ops:
- {worker: w1, id: op-1, thread: t1, after: start, input: {}}
- {id: op-2, worker: w2, thread: t2, after: start}
- {id: op-3, worker: w2, thread: t1, after: op-1}
target: {durability: sync, driver: async, saver: postgres}
seed: "seed-1"
cell: pg-async-sync-smoke
name: two-barriers
workload: smoke
v: 1
"""


def hashed(text: str) -> str:
    return scenario_sha256(parse_scenario(read_yaml(text)))


def test_equivalent_yaml_hashes_the_same() -> None:
    assert hashed(YAML) == hashed(EQUIVALENT)
    assert hashed(YAML) == scenario_sha256(parse_scenario(BASE))


@pytest.mark.parametrize(
    "changed",
    [
        YAML.replace("durability: sync", "durability: async"),
        YAML.replace("seed: seed-1", "seed: seed-2"),
        YAML.replace("cell: pg-async-sync-smoke", "cell: other"),
        YAML.replace("{pause: B2}", "{pause: B3}").replace("[B1, B2]", "[B1, B3]"),
        YAML.replace(
            "op: op-1}\n    do: {pause: B1}", "op: op-1, occurrence: 2}\n    do: {pause: B1}"
        ),
        YAML.replace("after: op-1}", "after: op-2}"),
        YAML + "timeouts: {run_s: 5}\n",
        YAML.replace("thread: t2, after: start}", "thread: t2, after: start, input: {n: 1}}"),
    ],
)
def test_a_change_of_meaning_changes_the_hash(changed: str) -> None:
    assert hashed(changed) != hashed(YAML)


def test_op_order_is_meaning() -> None:
    data = scenario()
    data["ops"][0], data["ops"][1] = data["ops"][1], data["ops"][0]
    assert scenario_sha256(parse_scenario(data)) != scenario_sha256(parse_scenario(BASE))


def test_a_non_finite_timeout_is_refused() -> None:
    assert "run_s" in refused(scenario(timeouts={"run_s": float("inf")}))


def test_a_non_finite_input_cannot_be_hashed_so_it_is_refused() -> None:
    data = scenario()
    data["ops"][0]["input"] = {"x": float("nan")}
    assert "non-finite" in refused(data)


def test_the_smoke_scenario_hash_is_pinned() -> None:
    # A change to the smoke scenario or to how scenarios are hashed shows up here first.
    smoke = load_scenario(REPO / "scenarios" / "smoke.yaml")
    assert isinstance(smoke, Scenario)
    assert scenario_sha256(smoke) == (
        "00de75ac9258c5af8a8b8ad44b2755364290d1ecd2cfb3ef5d427058bacf3761"
    )


# -- seeds ------------------------------------------------------------------------------------


def test_trial_seeds_are_deterministic_and_distinct() -> None:
    assert trial_seed("m", "c", 1) == trial_seed("m", "c", 1)
    seeds = {trial_seed("m", "c", n) for n in range(1, 51)}
    assert len(seeds) == 50
    assert trial_seed("m", "c", 1) != trial_seed("m", "d", 1) != trial_seed("n", "c", 1)


def test_trial_seed_parts_cannot_run_together() -> None:
    # Without a separator, ("ab", "c") and ("a", "bc") would encode the same bytes.
    assert trial_seed("ab", "c", 1) != trial_seed("a", "bc", 1)
    assert trial_seed("m", "c1", 1) != trial_seed("m", "c", 11)


def test_trial_seed_is_sha256_of_the_json_triple() -> None:
    import hashlib

    expected = hashlib.sha256(b'["smoke-v1","smoke",1]').hexdigest()
    assert trial_seed("smoke-v1", "smoke", 1) == expected
