"""Landmark extraction from fault-free histories, and the repository's draft table.

Run here on synthetic histories only, to show the extraction agrees with how failpoint hits are
counted and that its consistency checks fire. The table's occurrence numbers come from real
fault-free T-09 histories; nothing here asserts one.
"""

from __future__ import annotations

from crashcourt.checker.artifacts import TrialArtifacts
from crashcourt.checker.facts import data_list
from crashcourt.checker.landmarks import Site, locate, occurrences, repository_tables
from crashcourt.history.merge import group_by_process
from crashcourt.spec.events import Event
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_faults import trigger, with_scenario

(TABLE,) = repository_tables()


def by_proc(artifacts: TrialArtifacts) -> dict[str, tuple[Event, ...]]:
    return group_by_process(h.events for h in artifacts.histories)


def sites(artifacts: TrialArtifacts, point: str = "before") -> tuple[list[Site], list[str]]:
    return locate(TABLE, by_proc(artifacts), point)


def test_the_repository_table_is_audited_with_the_derived_occurrences() -> None:
    """The occurrences derived from T-09's real fault-free histories (research/evidence/t-10/)."""
    assert TABLE.status == "audited"
    assert {s.id: s.occurrence for s in TABLE.landmarks} == {
        "L-IN": 1,
        "L-RES": 2,
        "L-EXW": 3,
        "L-EXC": 2,
        "L-T1": 3,
        "L-GET": 1,
    }


def test_extraction_agrees_with_how_hits_are_counted() -> None:
    """The occurrence extraction derives for L-EXW from a fault-free trial is the one a hit at
    that call records, so an arm built from it fires there and its landmark verifies."""
    found, _ = sites(T.w1_trial())
    exw = next(s for s in found if s.landmark == "L-EXW")
    killed = T.kill_at_writes_trial("put_writes.before")
    hit = next(
        e for h in killed.histories for e in h.events if e.kind == "fp.hit" and e.proc == "worker-1"
    )
    assert (exw.worker, exw.op_id, exw.occurrence) == (
        "worker-1",
        "resume-1",
        hit.data["occurrence"],
    )
    arm = killed.scenario.arms[0].model_copy(update={"landmark": "L-EXW"})
    assert trigger(with_scenario(killed, arms=(arm,), landmarks=TABLE)) is None


def test_each_w1_landmark_is_one_call_per_run() -> None:
    found, problems = sites(T.w1_trial())
    located = {(s.landmark, s.op_id) for s in found}
    assert {
        ("L-IN", "start-1"),
        ("L-RES", "resume-1"),
        ("L-EXW", "resume-1"),
        ("L-EXC", "resume-1"),
    } <= located
    assert [p for p in problems if "no call matches" not in p] == []
    # W5's landmark has no call in W1.
    assert "L-T1: no call matches" in problems


def test_l_res_is_the_interrupted_tasks_resume_write() -> None:
    """Founder ruling of 2026-10-10: the put_writes carrying the RESUME special write for the
    interrupted task, not the null task's copy of the Command's value written before it."""
    found, _ = sites(T.w1_trial())
    site = next(s for s in found if s.landmark == "L-RES")
    trial = by_proc(T.w1_trial())
    call = trial[site.worker][int(site.pointer.rsplit("=", 1)[1])]
    assert call.task_id == "task-approval-T1"
    assert "__resume__" in data_list(call, "channels")
    # Without the task rule, 4.2's channel rule alone matches both writes.
    spec = next(s for s in TABLE.landmarks if s.id == "L-RES")
    loose = TABLE.model_copy(
        update={"landmarks": (spec.model_copy(update={"rules": spec.rules[:1]}),)}
    )
    _, problems = locate(loose, trial)
    assert any(p.startswith("L-RES: 2 calls match in worker-1 resume-1") for p in problems)


def test_w5_landmarks_are_found_in_every_turn() -> None:
    found, _ = sites(T.w5_fork_trial())
    turns = {s.op_id for s in found if s.landmark == "L-T1"}
    assert turns == {"turn-1", "turn-2", "turn-3"}


def test_occurrences_must_agree_across_trials() -> None:
    first, _ = sites(T.w1_trial())
    second, _ = sites(T.w1_trial())
    stable, problems = occurrences([first, second])
    assert problems == []
    assert ("L-EXW", "worker-1", "resume-1") in stable

    def extra_writes(events: list[Event]) -> list[Event]:
        """One more put_writes call in resume-1 before execute's, as an unstable run might."""
        index = next(
            i
            for i, e in enumerate(events)
            if e.kind == "node.start" and e.data.get("node") == "execute"
        )
        extra = events[index].model_copy(
            update={
                "kind": "saver.put_writes.call",
                "task_id": "task-other",
                "data": {"channels": ["x"], "task_path": ""},
            }
        )
        done = extra.model_copy(
            update={"kind": "saver.put_writes.return", "status": "ok", "data": {}}
        )
        return [*events[:index], extra, done, *events[index:]]

    moved, _ = sites(M.edit_history(T.w1_trial(), "worker-1", extra_writes))
    _, problems = occurrences([first, moved])
    assert any("L-EXW" in p and "differs between trials" in p for p in problems)
    _, problems = occurrences([first, [s for s in second if s.landmark != "L-EXC"]])
    assert any("L-EXC" in p and "found in 1 of 2 trials" in p for p in problems)


def test_after_points_are_located_too() -> None:
    found, _ = sites(T.w1_trial(), point="after")
    assert {(s.landmark, s.op_id) for s in found} >= {("L-EXW", "resume-1"), ("L-EXC", "resume-1")}
