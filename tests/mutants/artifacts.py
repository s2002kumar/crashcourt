"""Artifact mutants AM1-AM10 (red-team specification 3.4): labeled, offline edits to a copy of a
trial's artifacts. Never applied to target rows; they exist to show the checker reports exactly
the outcome each edit should produce.

Each mutant keeps the copy consistent everywhere except the one property it changes (seq numbers
are renumbered and the references to them follow; the ledger chain is re-linked), so the outcome
it provokes comes from the predicate under test and not from an unrelated integrity trigger. AM7
needs a re-run on a replacement worker, which an edit of a fault-free trial cannot produce
consistently; it is built as a synthetic trial in tests/unit/test_checker_mutants.py.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from crashcourt.checker.artifacts import (
    CheckpointRow,
    LedgerExport,
    LedgerRow,
    ProcessHistory,
    Snapshot,
    SnapshotPair,
    TrialArtifacts,
)
from crashcourt.spec.events import Event

EventEdit = Callable[[list[Event]], list[Event]]


def _renumber(artifacts: TrialArtifacts, proc: str, events: list[Event]) -> TrialArtifacts:
    """Replace one process's history, renumber its seq from 0, and update every reference to
    those seq numbers (client.ok/fail data.worker_seq, fp.release and fp.action data.hit_seq)."""
    mapping = {e.seq: i for i, e in enumerate(events)}

    def remap(old: int) -> int:
        kept = [s for s in mapping if s <= old]
        return mapping[max(kept)] if kept else 0

    def fix(event: Event) -> Event:
        data = dict(event.data)
        if event.kind in {"client.ok", "client.fail"} and data.get("worker") == proc:
            data["worker_seq"] = remap(int(str(data["worker_seq"])))
        if event.kind == "fp.release" and data.get("proc") == proc:
            data["hit_seq"] = remap(int(str(data["hit_seq"])))
        if event.kind == "fp.action" and event.proc == proc:
            data["hit_seq"] = remap(int(str(data["hit_seq"])))
        return event.model_copy(update={"data": data})

    histories: list[ProcessHistory] = []
    for history in artifacts.histories:
        if history.proc == proc:
            new = [fix(e).model_copy(update={"seq": i}) for i, e in enumerate(events)]
        else:
            new = [fix(e) for e in history.events]
        histories.append(history.model_copy(update={"events": tuple(new)}))
    return artifacts.model_copy(update={"histories": tuple(histories)})


def edit_history(artifacts: TrialArtifacts, proc: str, edit: EventEdit) -> TrialArtifacts:
    history = next(h for h in artifacts.histories if h.proc == proc)
    return _renumber(artifacts, proc, edit(list(history.events)))


def edit_snapshot(
    artifacts: TrialArtifacts, edit: Callable[[Snapshot], Snapshot]
) -> TrialArtifacts:
    pair = artifacts.snapshots
    assert pair is not None
    new = SnapshotPair(
        first=edit(pair.first), second=edit(pair.second), pretrial_rows=pair.pretrial_rows
    )
    return artifacts.model_copy(update={"snapshots": new})


def relink(rows: list[LedgerRow]) -> tuple[LedgerRow, ...]:
    """Renumber sink_seq from 1 and re-chain prev_hash and row_hash."""
    out: list[LedgerRow] = []
    prev = "0" * 64
    for seq, row in enumerate(rows, start=1):
        body = {
            "sink_seq": seq,
            "effect_key": row.effect_key,
            "op_id": row.op_id,
            "attempt": row.attempt,
            "worker": row.worker,
        }
        digest = hashlib.sha256(f"{prev}|{json.dumps(body, sort_keys=True)}".encode()).hexdigest()
        out.append(row.model_copy(update={"sink_seq": seq, "prev_hash": prev, "row_hash": digest}))
        prev = digest
    return tuple(out)


def _head(artifacts: TrialArtifacts) -> CheckpointRow:
    assert artifacts.snapshots is not None
    return max(artifacts.snapshots.first.checkpoints, key=lambda r: r.checkpoint_id)


def _by_id(artifacts: TrialArtifacts, checkpoint_id: str) -> CheckpointRow:
    assert artifacts.snapshots is not None
    return next(
        r for r in artifacts.snapshots.first.checkpoints if r.checkpoint_id == checkpoint_id
    )


# -- the mutants -----------------------------------------------------------------------------


def am1_delete_head(artifacts: TrialArtifacts) -> TrialArtifacts:
    """AM1: delete the head row. Expected: I1b (sync) VIOLATION_CANDIDATE; I6a GAP."""
    head = _head(artifacts).checkpoint_id
    return edit_snapshot(
        artifacts,
        lambda s: s.model_copy(
            update={"checkpoints": tuple(r for r in s.checkpoints if r.checkpoint_id != head)}
        ),
    )


def am2_delete_middle(artifacts: TrialArtifacts) -> TrialArtifacts:
    """AM2: delete a row that has a child. Expected: I5b GAP (dangling parent)."""
    head = _head(artifacts)
    middle = _by_id(artifacts, str(head.parent_checkpoint_id)).parent_checkpoint_id
    return edit_snapshot(
        artifacts,
        lambda s: s.model_copy(
            update={"checkpoints": tuple(r for r in s.checkpoints if r.checkpoint_id != middle)}
        ),
    )


def am3_second_child(artifacts: TrialArtifacts) -> TrialArtifacts:
    """AM3: add a second child to one parent (an id below the head). Expected: I5a GAP."""
    head = _head(artifacts)
    parent = _by_id(artifacts, str(head.parent_checkpoint_id))
    sibling = head.model_copy(
        update={
            "checkpoint_id": parent.checkpoint_id[:-4] + "ffff",
            "parent_checkpoint_id": parent.checkpoint_id,
        }
    )
    return edit_snapshot(
        artifacts, lambda s: s.model_copy(update={"checkpoints": (*s.checkpoints, sibling)})
    )


def am4_delete_blob(artifacts: TrialArtifacts, channel: str = "executed") -> TrialArtifacts:
    """AM4: delete a checkpoint_blobs row a checkpoint names. Expected: I5c GAP."""
    return edit_snapshot(
        artifacts,
        lambda s: s.model_copy(update={"blobs": tuple(b for b in s.blobs if b.channel != channel)}),
    )


def am5_delete_effect(artifacts: TrialArtifacts, key_prefix: str = "charge:") -> TrialArtifacts:
    """AM5: delete the charge row, with its append, request, and response. Expected (founder
    decision 4): INVALID by the I3 harness alarm."""
    assert artifacts.ledger is not None
    gone = [r for r in artifacts.ledger.rows if r.effect_key.startswith(key_prefix)]
    kept = [r for r in artifacts.ledger.rows if not r.effect_key.startswith(key_prefix)]
    out = artifacts.model_copy(update={"ledger": LedgerExport(rows=relink(kept), verify_ok=True)})
    for row in gone:
        out = edit_history(
            out,
            row.worker,
            lambda es, row=row: [
                e
                for e in es
                if not (e.kind.startswith("effect.") and e.effect_key == row.effect_key)
            ],
        )
    if any(h.proc == "sink" for h in out.histories):
        out = edit_history(
            out, "sink", lambda es: [e for e in es if not str(e.effect_key).startswith(key_prefix)]
        )
        out = _sink_from_ledger(out)
    return out


def _sink_from_ledger(artifacts: TrialArtifacts) -> TrialArtifacts:
    """Rewrite the sink's appends and the workers' ok responses to the relinked ledger rows."""
    assert artifacts.ledger is not None
    rows = {(r.worker, r.op_id, r.effect_key, r.attempt): r for r in artifacts.ledger.rows}
    histories: list[ProcessHistory] = []
    for history in artifacts.histories:
        events: list[Event] = []
        for e in history.events:
            if e.kind == "sink.append":
                row = rows[
                    (str(e.data["worker"]), str(e.op_id), str(e.effect_key), int(e.attempt or 0))
                ]
                e = e.model_copy(
                    update={"data": {**e.data, "sink_seq": row.sink_seq, "row_hash": row.row_hash}}
                )
            elif e.kind == "effect.response" and e.status == "ok":
                row = rows[(e.proc, str(e.op_id), str(e.effect_key), int(e.attempt or 0))]
                e = e.model_copy(update={"data": {**e.data, "sink_seq": row.sink_seq}})
            events.append(e)
        histories.append(history.model_copy(update={"events": tuple(events)}))
    return artifacts.model_copy(update={"histories": tuple(histories)})


def am6_duplicate_effect(artifacts: TrialArtifacts, key_prefix: str = "charge:") -> TrialArtifacts:
    """AM6: duplicate the charge with attempt 2 in the same execute run, no stop. Expected: I4 GAP,
    a duplicate with no documented cause (and a harness investigation)."""
    assert artifacts.ledger is not None
    first = next(r for r in artifacts.ledger.rows if r.effect_key.startswith(key_prefix))
    second = first.model_copy(update={"attempt": 2})
    rows = sorted([*artifacts.ledger.rows, second], key=lambda r: (r.sink_seq, r.attempt))
    out = artifacts.model_copy(update={"ledger": LedgerExport(rows=relink(rows), verify_ok=True)})

    def again(events: list[Event]) -> list[Event]:
        index = next(
            i
            for i, e in enumerate(events)
            if e.kind == "effect.response" and e.effect_key == first.effect_key
        )
        request = next(
            e for e in events if e.kind == "effect.request" and e.effect_key == first.effect_key
        )
        response = events[index]
        return [
            *events[: index + 1],
            request.model_copy(update={"attempt": 2}),
            response.model_copy(update={"attempt": 2}),
            *events[index + 1 :],
        ]

    out = edit_history(out, first.worker, again)

    def sink(events: list[Event]) -> list[Event]:
        index = next(i for i, e in enumerate(events) if e.effect_key == first.effect_key)
        return [
            *events[: index + 1],
            events[index].model_copy(update={"attempt": 2}),
            *events[index + 1 :],
        ]

    out = edit_history(out, "sink", sink)
    return _sink_from_ledger(out)


def am8_start_before_put_return(artifacts: TrialArtifacts, node: str = "execute") -> TrialArtifacts:
    """AM8: move a node.start of step k+1 before the put.return of step k (sync). Expected: I1a
    VIOLATION_CANDIDATE."""
    worker = next(
        h.proc
        for h in artifacts.histories
        if any(e.kind == "node.start" and e.data.get("node") == node for e in h.events)
    )

    def move(events: list[Event]) -> list[Event]:
        start = next(e for e in events if e.kind == "node.start" and e.data.get("node") == node)
        put_return = max(
            (e for e in events if e.kind == "saver.put.return" and e.seq < start.seq),
            key=lambda e: e.seq,
        )
        rest = [e for e in events if e is not start]
        at = rest.index(put_return)
        return [*rest[:at], start, *rest[at:]]

    return edit_history(artifacts, worker, move)


def am9_drop_heal(artifacts: TrialArtifacts) -> TrialArtifacts:
    """AM9: drop the fault.heal line. Expected: INVALID (trigger 9)."""
    orchestrator = next(
        h.proc for h in artifacts.histories if any(e.kind.startswith("client.") for e in h.events)
    )
    return edit_history(
        artifacts, orchestrator, lambda es: [e for e in es if e.kind != "fault.heal"]
    )


def am10_reorder(artifacts: TrialArtifacts, proc: str = "worker-1") -> TrialArtifacts:
    """AM10: swap two lines of one process, keeping their seq values. Expected: INVALID (trigger 2:
    seq not contiguous)."""
    histories: list[ProcessHistory] = []
    for history in artifacts.histories:
        events: list[Any] = list(history.events)
        if history.proc == proc:
            events[2], events[3] = events[3], events[2]
        histories.append(history.model_copy(update={"events": tuple(events)}))
    return artifacts.model_copy(update={"histories": tuple(histories)})
