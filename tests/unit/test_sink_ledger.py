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


# -- apply_once: the idempotent ledger ----------------------------------------------------------


@pytest.fixture
def once(tmp_path: Path) -> Any:
    led = Ledger(TRIAL.path(tmp_path), TRIAL, apply_once=True)
    yield led
    led.close()


def test_apply_once_applies_the_first_request_per_logical_op_and_records_the_rest(
    once: Ledger,
) -> None:
    first = once.append(effect(attempt=1, worker="worker-1"))
    second = once.append(effect(attempt=2, worker="worker-2"))
    other = once.append(effect(logical_op="refund:t3:i1", effect_key="refund:order-17"))
    assert (first["status"], first["applied"], first["duplicate_of"]) == ("applied", True, None)
    assert (second["status"], second["applied"], second["duplicate_of"]) == ("duplicate", False, 1)
    assert (other["status"], other["applied"], other["duplicate_of"]) == ("applied", True, None)
    rows = once.rows()
    assert [(r["sink_seq"], r["attempt"], r["status"]) for r in rows] == [
        (1, 1, "applied"),
        (2, 2, "duplicate"),
        (3, 1, "applied"),
    ]
    assert once.verify().ok
    # Both attempts are in the chain; nothing was deduplicated away.
    assert [r["worker"] for r in rows[:2]] == ["worker-1", "worker-2"]


def test_a_duplicates_status_is_hashed_so_editing_it_breaks_the_chain(once: Ledger) -> None:
    once.append(effect(attempt=1))
    once.append(effect(attempt=2))
    with sqlite3.connect(once.path) as conn:
        conn.execute("UPDATE effects SET status = 'duplicate' WHERE sink_seq = 1")
    verified = once.verify()
    assert not verified.ok and verified.first_bad == 1


def test_the_unique_index_is_the_backstop_for_a_second_applied_row(once: Ledger) -> None:
    once.append(effect(attempt=1))
    with sqlite3.connect(once.path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO effects SELECT 9, received_mono_ns, received_wall_ns, effect_key, "
            "logical_op, op_id, 2, worker, pid, thread_id, node, checkpoint_id_seen, "
            "payload_hash, 'applied', prev_hash, row_hash FROM effects WHERE sink_seq = 1"
        )


def test_apply_once_is_decided_under_the_exclusive_lock(once: Ledger) -> None:
    """Many threads request the same logical op at once: exactly one row is applied."""
    results: list[dict[str, Any]] = []
    lock = threading.Lock()

    def post(n: int) -> None:
        row = once.append(effect(attempt=n))
        with lock:
            results.append(row)

    threads = [threading.Thread(target=post, args=(n,)) for n in range(1, 17)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    applied = [r for r in results if r["applied"]]
    assert len(applied) == 1 and applied[0]["sink_seq"] == 1
    assert sorted(r["duplicate_of"] for r in results if not r["applied"]) == [1] * 15
    assert once.verify().ok and once.verify().rows == 16


def test_apply_once_persists_in_the_file_and_the_other_mode_is_refused(tmp_path: Path) -> None:
    from crashcourt.sink.ledger import LedgerConflict

    path = TRIAL.path(tmp_path)
    led = Ledger(path, TRIAL, apply_once=True)
    led.append(effect(attempt=1))
    led.close()
    with pytest.raises(LedgerConflict, match="was opened with apply_once; this opener asks"):
        Ledger(path, TRIAL)
    reopened = Ledger(path, TRIAL, apply_once=True)
    assert reopened.append(effect(attempt=2))["status"] == "duplicate"
    reopened.close()
    with sqlite3.connect(path) as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        indexes = [
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        ]
    assert meta["apply_once"] == "1" and "effects_applied_once" in indexes
    plain = TrialId(batch="DEMO-b", cell="DEMO-c", trial=4, seed="s")
    Ledger(plain.path(tmp_path), plain).close()
    with pytest.raises(LedgerConflict, match="was opened without apply_once; this opener asks"):
        Ledger(plain.path(tmp_path), plain, apply_once=True)


def test_a_plain_ledger_is_unchanged_by_the_apply_once_code(ledger: Ledger) -> None:
    """The baseline: no index, no mode in meta, every row applied, and the answer says so."""
    rows = [ledger.append(effect(attempt=n)) for n in (1, 2, 2)]
    assert [(r["status"], r["applied"], r["duplicate_of"]) for r in rows] == [
        ("applied", True, None)
    ] * 3
    with sqlite3.connect(ledger.path) as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        schema = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            ).fetchall()
        ]
    assert "apply_once" not in meta and schema == []
    assert ledger.apply_once is False


def test_only_a_duplicate_row_names_its_status_in_the_sink_history(once: Ledger) -> None:
    from crashcourt.history.writer import RunIdentity

    once.append(effect(attempt=1))
    once.append(effect(attempt=2))
    run = RunIdentity(TRIAL.batch, TRIAL.cell, TRIAL.trial, TRIAL.seed)
    events = ledger_events(once.rows(), run, pid=99)
    assert [e.kind for e in events] == ["sink.append", "sink.append"]
    assert "status" not in events[0].data
    assert events[1].data["status"] == "duplicate"
