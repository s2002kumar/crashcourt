"""The trial-level INVALID triggers (1.2): each fires on its own condition and names itself."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from crashcourt.checker.artifacts import SinkHealth, Snapshot, SnapshotPair, TrialArtifacts
from crashcourt.checker.decode import encode
from crashcourt.spec.events import Event
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_faults import trigger, with_scenario


def history(
    artifacts: TrialArtifacts, proc: str, change: Callable[[list[Event]], list[Event]]
) -> TrialArtifacts:
    return M.edit_history(artifacts, proc, change)


def snapshot(artifacts: TrialArtifacts, change: Callable[[Snapshot], Snapshot]) -> TrialArtifacts:
    return M.edit_snapshot(artifacts, change)


def update(artifacts: TrialArtifacts, **changes: Any) -> TrialArtifacts:
    return artifacts.model_copy(update=changes)


def drop(kind: str, which: int = 0) -> Callable[[list[Event]], list[Event]]:
    def edit(events: list[Event]) -> list[Event]:
        target = [e for e in events if e.kind == kind][which]
        return [e for e in events if e is not target]

    return edit


def _executed_as(value: object) -> Callable[[Snapshot], Snapshot]:
    type_, blob = encode(value)
    return lambda s: s.model_copy(
        update={
            "blobs": tuple(
                b.model_copy(update={"type": type_, "blob": blob}) if b.channel == "executed" else b
                for b in s.blobs
            )
        }
    )


def _snapshots(artifacts: TrialArtifacts) -> SnapshotPair:
    assert artifacts.snapshots is not None
    return artifacts.snapshots


def meta(**changes: Any) -> Callable[[Snapshot], Snapshot]:
    return lambda s: s.model_copy(update={"meta": s.meta.model_copy(update=changes)})


BASE = T.w1_trial()
assert BASE.ledger is not None and BASE.snapshots is not None and BASE.manifest is not None
LEDGER, SNAPSHOTS, MANIFEST = BASE.ledger, BASE.snapshots, BASE.manifest


def test_the_base_trial_is_valid() -> None:
    assert trigger(BASE) is None


CASES: list[tuple[str, int, Callable[[], TrialArtifacts]]] = [
    # 1: completeness
    ("no snapshots", 1, lambda: update(BASE, snapshots=None)),
    ("no manifest", 1, lambda: update(BASE, manifest=None)),
    (
        "a dispatched worker without history",
        1,
        lambda: update(BASE, histories=tuple(h for h in BASE.histories if h.proc != "worker-1")),
    ),
    (
        "an op the scenario does not declare",
        1,
        lambda: with_scenario(BASE, ops=BASE.scenario.ops[:1]),
    ),
    (
        "a declared op never invoked",
        1,
        lambda: with_scenario(
            BASE,
            ops=(*BASE.scenario.ops, BASE.scenario.ops[1].model_copy(update={"op_id": "resume-9"})),
        ),
    ),
    ("a thread the snapshot does not cover", 1, lambda: snapshot(BASE, meta(threads=("T9",)))),
    ("no sink reading after the trial", 1, lambda: update(BASE, sink_health=())),
    # 2: histories
    (
        "an unparseable line",
        2,
        lambda: update(
            BASE,
            histories=tuple(
                h.model_copy(update={"parse_error": "line 3"}) if h.proc == "worker-1" else h
                for h in BASE.histories
            ),
        ),
    ),
    (
        "a partial line without a signal",
        2,
        lambda: update(
            BASE,
            histories=tuple(
                h.model_copy(update={"partial_tail": b'{"v":1'}) if h.proc == "worker-1" else h
                for h in BASE.histories
            ),
        ),
    ),
    ("a seq gap", 2, lambda: M.am10_reorder(BASE)),
    # 3: contradictions
    (
        "a ledger row without its request",
        3,
        lambda: update(
            BASE,
            ledger=LEDGER.model_copy(
                update={"rows": tuple(r.model_copy(update={"pid": 1}) for r in LEDGER.rows)}
            ),
        ),
    ),
    (
        "an append the ledger does not hold",
        3,
        lambda: update(BASE, ledger=LEDGER.model_copy(update={"rows": ()})),
    ),
    # 4: client records
    (
        "an invoke with two terminal events",
        4,
        lambda: history(
            BASE, "orchestrator", lambda es: [*es, next(e for e in es if e.kind == "client.ok")]
        ),
    ),
    (
        "an ok without the result edge",
        4,
        lambda: history(
            BASE,
            "orchestrator",
            lambda es: [
                e.model_copy(update={"data": {}}) if e.kind == "client.ok" else e for e in es
            ],
        ),
    ),
    # 5: effects
    (
        "a request with no response from a live worker",
        5,
        lambda: history(BASE, "worker-1", drop("effect.response")),
    ),
    # 6: sink
    (
        "a failed response",
        6,
        lambda: history(
            BASE,
            "worker-1",
            lambda es: [
                e.model_copy(update={"status": "fail", "error": "500", "data": {}})
                if e.kind == "effect.response"
                else e
                for e in es
            ],
        ),
    ),
    (
        "sink hooks enabled",
        6,
        lambda: update(
            BASE, sink_health=(BASE.sink_health[0].model_copy(update={"hooks_enabled": True}),)
        ),
    ),
    (
        "another sink source",
        6,
        lambda: update(
            BASE, sink_health=(BASE.sink_health[0].model_copy(update={"source_sha256": "c" * 64}),)
        ),
    ),
    (
        "another open trial",
        6,
        lambda: update(
            BASE,
            sink_health=(
                SinkHealth(
                    when="after",
                    hooks_enabled=False,
                    source_sha256=T.SINK_SOURCE,
                    open_trial="x/y/1",
                ),
            ),
        ),
    ),
    (
        "ledger verify failed",
        6,
        lambda: update(BASE, ledger=LEDGER.model_copy(update={"verify_ok": False})),
    ),
    (
        "a broken chain",
        6,
        lambda: update(
            BASE,
            ledger=LEDGER.model_copy(
                update={
                    "rows": tuple(r.model_copy(update={"prev_hash": "d" * 64}) for r in LEDGER.rows)
                }
            ),
        ),
    ),
    # 7: snapshot
    ("a snapshot before quiescence", 7, lambda: snapshot(BASE, meta(after_orchestrator_seq=3))),
    ("toxics at snapshot time", 7, lambda: snapshot(BASE, meta(toxics_installed=("latency",)))),
    ("a proxied snapshot", 7, lambda: snapshot(BASE, meta(dsn="postgresql://proxy"))),
    (
        "two snapshots that differ",
        7,
        lambda: update(
            BASE,
            snapshots=SNAPSHOTS.model_copy(
                update={"second": _snapshots(M.am1_delete_head(BASE)).first}
            ),
        ),
    ),
    (
        "a blob that fails strict decoding",
        7,
        lambda: snapshot(
            BASE,
            lambda s: s.model_copy(
                update={
                    "blobs": tuple(
                        b.model_copy(update={"blob": b"\xc1garbage"})
                        if b.channel == "executed"
                        else b
                        for b in s.blobs
                    )
                }
            ),
        ),
    ),
    ("a value of the wrong shape", 7, lambda: snapshot(BASE, _executed_as({"not": "a list"}))),
    # 8: pre-trial rows
    (
        "rows before the trial",
        8,
        lambda: update(BASE, snapshots=SNAPSHOTS.model_copy(update={"pretrial_rows": {"T1": 2}})),
    ),
    (
        "no emptiness check",
        8,
        lambda: update(BASE, snapshots=SNAPSHOTS.model_copy(update={"pretrial_rows": {}})),
    ),
    # 9: inject and heal
    (
        "a failed inject",
        9,
        lambda: history(
            T.kill_mid_execute_trial(),
            "orchestrator",
            lambda es: [
                e.model_copy(update={"status": "fail", "error": "no"})
                if e.kind == "fault.inject"
                else e
                for e in es
            ],
        ),
    ),
    (
        "a failed heal",
        9,
        lambda: history(
            T.kill_mid_execute_trial(),
            "orchestrator",
            lambda es: [
                e.model_copy(update={"status": "fail", "error": "no"})
                if e.kind == "fault.heal"
                else e
                for e in es
            ],
        ),
    ),
    # 13: environment
    ("another manifest", 13, lambda: update(BASE, manifest_sha256="e" * 64)),
    ("another runner", 13, lambda: with_scenario(BASE, runner="github-hosted")),
    (
        "fsync off in the manifest",
        13,
        lambda: update(
            BASE,
            manifest=MANIFEST.model_copy(
                update={"postgres": {**MANIFEST.postgres, "fsync": "off"}}
            ),
        ),
    ),
    (
        "commit timestamps off at snapshot time",
        13,
        lambda: snapshot(
            BASE,
            meta(
                settings={
                    "fsync": "on",
                    "synchronous_commit": "on",
                    "track_commit_timestamp": "off",
                }
            ),
        ),
    ),
]


@pytest.mark.parametrize(("name", "number", "make"), CASES, ids=[c[0] for c in CASES])
def test_trigger(name: str, number: int, make: Callable[[], TrialArtifacts]) -> None:
    assert trigger(make()) == number


def test_a_killed_worker_may_end_in_a_partial_line() -> None:
    killed = T.kill_mid_execute_trial()
    tailed = update(
        killed,
        histories=tuple(
            h.model_copy(update={"partial_tail": b'{"v":1,"ba'}) if h.proc == "worker-1" else h
            for h in killed.histories
        ),
    )
    assert trigger(tailed) is None


def test_a_killed_worker_may_leave_a_request_unanswered() -> None:
    killed = T.kill_mid_execute_trial()
    assert trigger(history(killed, "worker-1", drop("effect.response"))) != 5
