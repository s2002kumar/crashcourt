"""The first matrix's held-barrier schedules, judged before the freeze on synthetic trials.

K3 and N8 are specified with deterministic held barriers, not SIGSTOP (founder ruling,
2026-10-10): the stale worker is held at a put.before, the op is taken over by a second worker
that completes, and the stale worker is released. K1a's replication is matched on its observable
shape (two charge rows) whatever it is classified as. Synthetic: the arms name no landmark here;
the landmarks and their occurrences come from the audit of real fault-free histories.
"""

from __future__ import annotations

from crashcourt.checker.artifacts import ArmSpec, FaultSpec, TrialArtifacts
from tests.unit import _trials as T
from tests.unit.test_checker_engine import NAU, omitted, outcomes, run

SYNC_PASS = ("PASS", "LG-DUR-SYNC")


def held_takeover_w5() -> TrialArtifacts:
    """K3 re-specified: worker-1 held at turn-1's last put.before; turn-1 redelivered to worker-2,
    which completes; worker-1 released, and its put lands too."""
    t = T.TrialBuilder(workload="w5")
    t.invoke(t.op("turn-1", "turn"))
    t.dispatch("turn-1", "worker-1")
    w1 = t.workers["worker-1"]
    t.get_tuple(w1, "turn-1", "T1")
    cin = t.put(
        w1, "turn-1", "T1", source="input", updates={"__start__": {"messages": ["human 1"]}}
    )
    t.node(w1, "turn-1", "respond", "task-respond-T1-1")
    reply = ["human 1", "ai 1 [attempt 1]"]
    t.put_writes(w1, "turn-1", "T1", cin, "task-respond-T1-1", [("messages", reply)])
    held = t.put(w1, "turn-1", "T1", source="loop", updates={"messages": reply}, hold=True)
    occurrence = int(str(t.last_hit("worker-1").data["occurrence"]))
    t.dispatch("turn-1", "worker-2", redeliver=True)
    t.w5_turn("worker-2", "turn-1", "T1", 1)
    t.ok("turn-1", "worker-2")
    t.release_put(w1, "turn-1", held)
    t.exit("worker-1")
    t.exit("worker-2")
    t.arms.append(
        ArmSpec(
            point="put.before",
            action="pause",
            worker="worker-1",
            op_id="turn-1",
            occurrence=occurrence,
        )
    )
    t.faults.extend(
        [
            FaultSpec(kind="pause", arm=0),
            FaultSpec(kind="redelivery", op_ids=("turn-1",), worker="worker-2"),
        ]
    )
    return t.build()


def held_stale_worker_w1() -> TrialArtifacts:
    """N8 re-specified: worker-1 runs resume-1 through execute (charge attempt 1) and is held at
    the put after execute; resume-1 is redelivered to worker-2, which completes (charge attempt
    2); worker-1 is released and finishes its own branch."""
    t = T.TrialBuilder()
    t.invoke(t.op("start-1", "start"))
    t.dispatch("start-1", "worker-1")
    t.w1_start("worker-1", "start-1")
    t.ok("start-1", "worker-1")
    t.invoke(t.op("resume-1", "resume", resume="approve"))
    t.dispatch("resume-1", "worker-1")
    t.w1_approve("worker-1", "resume-1", "T1", "approve")
    _, attempt = t.w1_execute("worker-1", "resume-1", "T1", "approve")
    assert attempt == 1
    w1 = t.workers["worker-1"]
    receipt = ["charge:T1", 1]
    held = t.put(w1, "resume-1", "T1", source="loop", updates={"receipt": receipt}, hold=True)
    occurrence = int(str(t.last_hit("worker-1").data["occurrence"]))
    graph = t.graphs["T1"]
    branch = (dict(graph.values), dict(graph.versions), graph.step + 1)
    t.dispatch("resume-1", "worker-2", redeliver=True)
    t.w1_resume("worker-2", "resume-1", "T1", "approve")
    t.ok("resume-1", "worker-2")
    t.release_put(w1, "resume-1", held)
    graph.head, (graph.values, graph.versions, graph.step) = held, branch
    record = t.task("T1", "record")
    t.node(w1, "resume-1", "record", record)
    t.put_writes(w1, "resume-1", "T1", held, record, [("executed", [receipt])])
    t.put(w1, "resume-1", "T1", source="loop", updates={"executed": [receipt]})
    t.exit("worker-1")
    t.exit("worker-2")
    t.arms.append(
        ArmSpec(
            point="put.before",
            action="pause",
            worker="worker-1",
            op_id="resume-1",
            occurrence=occurrence,
        )
    )
    t.faults.extend(
        [
            FaultSpec(kind="pause", arm=0),
            FaultSpec(kind="redelivery", op_ids=("resume-1",), worker="worker-2"),
        ]
    )
    return t.build()


def test_k3_held_takeover_forks_and_keeps_durability() -> None:
    out = run(held_takeover_w5())
    assert out.report.invalid is None
    got = outcomes(out)
    assert got["I5a"] == ("GAP", None)
    assert got["I6a"] == ("GAP", None)
    # Two writers: what a second run does is disclaimed, durability is not (ruling D1).
    assert got["I1a"] == got["I1b"] == got["I11"] == SYNC_PASS


def test_n8_held_stale_worker_is_gap_with_two_charges_and_a_fork() -> None:
    out = run(held_stale_worker_w1())
    assert out.report.invalid is None
    got = outcomes(out)
    assert got["I4"] == ("GAP", None)
    assert got["I5a"] == ("GAP", None)
    assert got["I6a"] == ("GAP", None)
    assert got["I7a"] == ("GAP", None)
    assert got["I1a"] == got["I1b"] == got["I11"] == SYNC_PASS
    i4 = next(d for d in out.report.details if d.invariant == "I4")
    assert [a.outcome for a in i4.anomalies] == ["GAP"]
    assert "attempt 2 after attempt 1" in i4.anomalies[0].reason
    assert not omitted(out) & {"I1a", "I1b", "I4", "I5a", "I6a", "I7a"}


def test_k1a_is_gap_and_shows_its_published_shape() -> None:
    """K1a / C11-8039: SIGKILL at L-EXW's put_writes.before and redelivery. Under the ruling on
    running nodes the duplicate is GAP, and checker.json's detail carries what the replication is
    matched on: two charge rows, attempts 1 and 2, with their ledger pointers."""
    out = run(T.kill_at_writes_trial("put_writes.before"))
    got = outcomes(out)
    assert got["I4"] == got["I7a"] == ("GAP", None)
    assert got["I1b"] == SYNC_PASS
    i4 = next(d for d in out.report.details if d.invariant == "I4")
    (duplicate,) = i4.anomalies
    assert "charge:T1: attempt 2 after attempt 1" in duplicate.reason
    assert [p.split("#")[0] for p in duplicate.evidence] == ["ledger", "ledger"]
    assert got.get("I2", NAU) == NAU
