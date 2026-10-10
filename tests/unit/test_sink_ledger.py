from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from crashcourt.sink.ledger import EffectRequest, Ledger, TrialId, ledger_events

TRIAL = TrialId(batch="DEMO-b", cell="DEMO-c", trial=3, seed="s")


def effect(**changes: Any) -> EffectRequest:
    fields: dict[str, Any] = {
        "effect_key": "charge:order-17",
        "logical_op": "approve:t3:i1",
        "op_id": "op-7",
        "attempt": 1,
        "worker": "worker-1",
        "pid": 4242,
        "thread_id": "t3",
        "node": "execute",
        "checkpoint_id_seen": "C4",
        "payload_hash": hashlib.sha256(b"charge 17").hexdigest(),
    }
    return EffectRequest(**{**fields, **changes})


@pytest.fixture
def ledger(tmp_path: Path) -> Any:
    led = Ledger(TRIAL.path(tmp_path), TRIAL)
    yield led
    led.close()


def test_identical_effects_are_never_deduplicated(ledger: Ledger) -> None:
    first = ledger.append(effect())
    second = ledger.append(effect())
    assert (first["sink_seq"], second["sink_seq"]) == (1, 2)
    assert [r["effect_key"] for r in ledger.rows()] == ["charge:order-17"] * 2
    assert ledger.verify().ok


def test_rows_carry_the_spec_fields_and_a_chain(ledger: Ledger) -> None:
    row = ledger.append(effect(attempt=2))
    assert row["attempt"] == 2 and row["status"] == "applied"
    assert row["prev_hash"] == "0" * 64 and len(row["row_hash"]) == 64
    [stored] = ledger.rows()
    assert stored["row_hash"] == row["row_hash"]
    assert stored["received_mono_ns"] > 0


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE effects SET attempt = 1 WHERE sink_seq = 2",
        "UPDATE effects SET effect_key = 'refund:order-17' WHERE sink_seq = 2",
        "DELETE FROM effects WHERE sink_seq = 2",
        "UPDATE effects SET sink_seq = 9 WHERE sink_seq = 1",
    ],
)
def test_editing_any_record_fails_verification(tmp_path: Path, tamper: str) -> None:
    path = TRIAL.path(tmp_path)
    led = Ledger(path, TRIAL)
    for attempt in (1, 2, 3):
        led.append(effect(attempt=attempt))
    led.close()
    with sqlite3.connect(path) as conn:
        conn.execute(tamper)
    reopened = Ledger(path, TRIAL)
    result = reopened.verify()
    reopened.close()
    assert not result.ok and result.first_bad is not None


def test_attempt_counters_persist_across_reopen(tmp_path: Path) -> None:
    path = TRIAL.path(tmp_path)
    led = Ledger(path, TRIAL)
    assert [led.next_attempt("t3/execute/0") for _ in range(2)] == [1, 2]
    assert led.next_attempt("t3/plan/0") == 1
    led.close()
    again = Ledger(path, TRIAL)
    assert again.next_attempt("t3/execute/0") == 3
    again.close()


def test_rows_persist_across_reopen(tmp_path: Path) -> None:
    path = TRIAL.path(tmp_path)
    led = Ledger(path, TRIAL)
    led.append(effect())
    led.close()
    again = Ledger(path, TRIAL)
    assert len(again.rows()) == 1 and again.append(effect())["sink_seq"] == 2
    assert again.verify().ok
    again.close()


def test_a_ledger_file_belongs_to_one_trial(tmp_path: Path) -> None:
    path = TRIAL.path(tmp_path)
    Ledger(path, TRIAL).close()
    with pytest.raises(ValueError, match="another trial"):
        Ledger(path, TrialId("DEMO-b", "DEMO-c", 4, "s"))


def test_concurrent_appends_keep_a_contiguous_chain(ledger: Ledger) -> None:
    def burst() -> None:
        for _ in range(50):
            ledger.append(effect())

    threads = [threading.Thread(target=burst) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [r["sink_seq"] for r in ledger.rows()] == list(range(1, 201))
    assert ledger.verify().ok


@pytest.mark.parametrize(
    "bad",
    [{"attempt": 0}, {"effect_key": ""}, {"payload_hash": "xyz"}, {"op_id": ""}],
)
def test_malformed_requests_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        effect(**bad)


@pytest.mark.parametrize("sink_clock", ["before the request", "after the response"])
def test_ledger_rows_become_sink_history_that_merges_causally(
    ledger: Ledger, tmp_path: Path, sink_clock: str
) -> None:
    from crashcourt.history.merge import merge
    from crashcourt.history.writer import HistoryWriter, RunIdentity

    run = RunIdentity(TRIAL.batch, TRIAL.cell, TRIAL.trial, TRIAL.seed)
    fields = {"op_id": "op-7", "effect_key": "charge:order-17", "attempt": 1}
    with HistoryWriter(tmp_path / "worker-1.jsonl", run=run, proc="worker-1") as worker:
        request = worker.emit("effect.request", **fields)
        row = ledger.append(effect())
        response = worker.emit(
            "effect.response", **fields, status="ok", data={"sink_seq": row["sink_seq"]}
        )
    # Put the sink's clock where only the causal edges can order it: before the request, or
    # after the response. Without the request-to-append or append-to-response edge, the merge
    # would follow mono_ns and misplace the append.
    rows = ledger.rows()
    shift = request.mono_ns - 1 if sink_clock == "before the request" else response.mono_ns + 1
    rows[0]["received_mono_ns"] = shift
    sink_events = ledger_events(rows, run, pid=99)
    assert [(e.kind, e.seq, e.data["worker"], e.data["sink_seq"]) for e in sink_events] == [
        ("sink.append", 0, "worker-1", 1)
    ]
    merged = merge([[request, response], sink_events])
    assert [e.kind for e in merged] == ["effect.request", "sink.append", "effect.response"]


@pytest.mark.parametrize("field", ["logical_op", "pid", "thread_id", "node"])
def test_effect_fields_the_ledger_needs_are_required(field: str) -> None:
    with pytest.raises(ValueError):
        effect(**{field: None})


def test_meta_is_written_once_and_kept(tmp_path: Path) -> None:
    path = TRIAL.path(tmp_path)
    Ledger(path, TRIAL).close()
    Ledger(path, TRIAL).close()  # reopening finds the meta and leaves it
    with sqlite3.connect(path) as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert meta == {
        "ledger_version": "2",
        "batch": "DEMO-b",
        "cell": "DEMO-c",
        "trial": "3",
        "seed": "s",
    }


def test_a_ledger_that_cannot_use_wal_is_refused() -> None:
    # SQLite keeps an in-memory database's journal in memory and answers "memory" to WAL.
    with pytest.raises(ValueError, match="journal_mode is memory, not wal"):
        Ledger(Path(":memory:"), TRIAL)


def test_a_file_from_another_ledger_version_is_refused(tmp_path: Path) -> None:
    from crashcourt.sink.ledger import LedgerConflict

    path = TRIAL.path(tmp_path)
    Ledger(path, TRIAL).close()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'ledger_version'")
    with pytest.raises(
        LedgerConflict, match="written by ledger version 1; this sink writes version 2"
    ):
        Ledger(path, TRIAL)


def test_a_file_for_another_seed_is_refused(tmp_path: Path) -> None:
    from crashcourt.sink.ledger import LedgerConflict

    path = TRIAL.path(tmp_path)
    Ledger(path, TRIAL).close()
    with pytest.raises(LedgerConflict, match="another trial"):
        Ledger(path, TrialId(TRIAL.batch, TRIAL.cell, TRIAL.trial, "s2"))
