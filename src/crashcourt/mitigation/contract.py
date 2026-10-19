"""The mitigation contract: what the N8 mitigation study measures in one trial, read from the
oracle's records and the histories, independently of the frozen checker.

The checker (v1) stays what it is and its outcomes are reported beside these measurements,
never changed by them. The contract reads the same files the checker reads, but asks its own
questions, each a count or a name a trial either shows or does not:

Checkpoints (snapshot.json, the direct read of the saver's tables after quiescence):
- `fork`: a checkpoint of the thread with two or more children.
- `head`: the checkpoint with the largest id, which is what the saver's "latest" read selects
  (`ORDER BY checkpoint_id DESC`); `head_writer`: the run (worker, op) whose `saver.put.return`
  acknowledged it; `head_attempts`: the attempt labels the head's state names in its `executed`
  channel (W1's record of every charge: [effect key, attempt]).
- `acknowledged_is_head`: the head was written by the run whose answer the client received.

The stale run (the run an op was redelivered away from, while it was still alive):
- `resumed`: it went on after the takeover, which the histories show causally: the redelivery is
  recorded before the releases of the holds it waited for, and the stale run's `fp.action` for
  those holds is recorded before what it did next. No clock is read.
- `attempted`: its checkpoint puts and task writes that ended (returned or raised) after it
  resumed; `durable`: those that returned ok and whose rows are in the snapshot, except a task
  write another run also acknowledged for the same (checkpoint, task), which the saver's
  `ON CONFLICT DO NOTHING` turned into no row of the stale run's; `fenced`: those that raised the
  fence's error (crashcourt.mitigation.fence); `other_errors`: those that raised anything else.

Effects (ledger.json, the sink's own export, and the workers' `effect.request` events):
- per logical op: `requested` (the requests any worker made), `applied` and `duplicates` (the
  ledger's rows by status), the attempts of each.
- `logical`: EXACTLY_ONCE (one applied), DUPLICATED (more), LOST (the client was answered ok
  and none applied), NONE (nothing requested). The names follow mstevens843/crashpoint.
- `attempt_consistency`: with exactly one applied row, whether the head's `executed` channel
  names its attempt: CONSISTENT, INCONSISTENT, or NOT_APPLICABLE. Judged apart from `logical`,
  never folded into it.

The fence (fence.json, the owner rows after the trial, when the batch had a fence):
- `owner_epoch` of the thread, the `acknowledged_epoch` the dispatcher issued to the run whose
  answer the client received, and whether they agree.

A trial the contract cannot read in full (a missing file, two threads, two redeliveries) is
recorded with its `problems` and no measurements are invented for it.
"""

from __future__ import annotations

import functools
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from crashcourt.history.reader import HistoryError, read_history
from crashcourt.mitigation.fence import OWNERS_FILE, is_fence_error
from crashcourt.spec.events import Event

#: The contract's name, recorded on every record it writes; a change of any definition above
#: is a new name.
CONTRACT = "n8-mitigation-v1"
CONTRACT_FILE = "contract.json"
SUMMARY_FILE = "mitigation.json"
#: W1's channel that records every charge as [effect key, attempt].
EXECUTED_CHANNEL = "executed"
#: The outcome names, from worst to best, for `logical`.
LOGICAL_ORDER = ("DUPLICATED", "LOST", "EXACTLY_ONCE", "NONE")

Run = tuple[str, str]  # (worker, op)


@functools.cache
def _serializer() -> Any:
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(
        pickle_fallback=False, allowed_json_modules=None, allowed_msgpack_modules=None
    )


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _events(directory: Path, proc: str) -> list[Event]:
    return sorted(read_history(directory / f"{proc}.jsonl").events, key=lambda e: e.seq)


def _run_of(e: Event) -> Run:
    return (e.proc, str(e.op_id))


class _Unreadable(Exception):
    pass


def evaluate_trial(
    directory: Path, *, fence: str | None, idempotent_effects: bool
) -> dict[str, Any]:
    """The contract's record of one trial directory."""
    record: dict[str, Any] = {
        "v": 1,
        "contract": CONTRACT,
        "variant": {"fence": fence, "idempotent_effects": idempotent_effects},
        "problems": [],
    }
    try:
        _fill(directory, record)
    except _Unreadable as exc:
        record["problems"].append(str(exc))
    except (OSError, ValueError, KeyError, TypeError, HistoryError) as exc:
        record["problems"].append(f"{type(exc).__name__}: {exc}")
    return record


def _fill(directory: Path, record: dict[str, Any]) -> None:
    trial = _json(directory / "trial.json")
    record["trial"] = int(trial["trial"])
    ops = cast(dict[str, dict[str, Any]], trial.get("ops") or {})
    threads = sorted({str(o["thread_id"]) for o in ops.values()})
    if len(threads) != 1:
        raise _Unreadable(f"the contract covers one thread; the trial ran {len(threads)}")
    [thread] = threads
    record["thread"] = thread
    workers = sorted(str(w) for w in cast(dict[str, Any], trial.get("workers") or {}))
    histories = {w: _events(directory, w) for w in workers}
    orchestrator = _events(directory, "orchestrator")

    # The run the client's answer came from, and the run an op was taken away from.
    redeliveries = [e for e in orchestrator if e.kind == "dispatch.redeliver"]
    if len(redeliveries) > 1:
        raise _Unreadable(
            f"the contract covers one redelivery; the trial recorded {len(redeliveries)}"
        )
    redelivery = redeliveries[0] if redeliveries else None
    op = str(redelivery.op_id) if redelivery is not None else str(list(ops)[-1])
    answer = next(
        (
            e
            for e in orchestrator
            if e.kind.startswith("client.") and e.kind != "client.invoke" and e.op_id == op
        ),
        None,
    )
    acknowledged: Run | None = None
    if answer is not None and answer.kind == "client.ok":
        acknowledged = (str(answer.data.get("worker")), op)
    record["op"] = op
    record["acknowledged"] = (
        {"worker": acknowledged[0], "op": op, "worker_seq": answer.data.get("worker_seq")}
        if acknowledged is not None and answer is not None
        else {"outcome": answer.kind.removeprefix("client.") if answer is not None else None}
    )

    snapshot = _json(directory / "snapshot.json")["second"]
    rows = [r for r in snapshot["checkpoints"] if r["thread_id"] == thread]
    writes = [w for w in snapshot["writes"] if w["thread_id"] == thread]
    blobs = [b for b in snapshot["blobs"] if b["thread_id"] == thread]
    record["checkpoints"] = _checkpoints(rows, blobs, histories, acknowledged)
    record["stale"] = _stale(redelivery, orchestrator, histories, rows, writes)
    record["effects"] = _effects(
        directory, histories, acknowledged, record["checkpoints"].get("head_attempts")
    )
    record["logical"] = min(
        (e["logical"] for e in record["effects"]),
        key=LOGICAL_ORDER.index,
        default="NONE",
    )
    record["fence"] = _fence(directory, thread, orchestrator, acknowledged)
    result = directory / "result.json"
    record["v1"] = (
        {str(r["invariant"]): str(r["outcome"]) for r in _json(result).get("results", [])}
        if result.exists()
        else None
    )


def _checkpoints(
    rows: Sequence[Mapping[str, Any]],
    blobs: Sequence[Mapping[str, Any]],
    histories: Mapping[str, Sequence[Event]],
    acknowledged: Run | None,
) -> dict[str, Any]:
    children: dict[str, list[str]] = {}
    for r in rows:
        parent = r.get("parent_checkpoint_id")
        if parent is not None:
            children.setdefault(str(parent), []).append(str(r["checkpoint_id"]))
    forks = {p: sorted(c) for p, c in children.items() if len(c) > 1}
    if not rows:
        return {"rows": 0, "fork": False, "forks": {}, "head": None}
    head = max(str(r["checkpoint_id"]) for r in rows)
    writer = next(
        (
            _run_of(e)
            for events in histories.values()
            for e in events
            if e.kind == "saver.put.return" and e.status == "ok" and e.checkpoint_id == head
        ),
        None,
    )
    head_row = next(r for r in rows if str(r["checkpoint_id"]) == head)
    return {
        "rows": len(rows),
        "fork": bool(forks),
        "forks": forks,
        "head": head,
        "head_writer": list(writer) if writer is not None else None,
        "head_attempts": _executed(head_row, blobs),
        "acknowledged_is_head": writer is not None and writer == acknowledged,
    }


def _executed(row: Mapping[str, Any], blobs: Sequence[Mapping[str, Any]]) -> list[int] | None:
    """The attempts W1's `executed` channel names at this checkpoint, or None without it."""
    checkpoint = cast(dict[str, Any], row.get("checkpoint") or {})
    inline = cast(dict[str, Any], checkpoint.get("channel_values") or {})
    versions = cast(dict[str, Any], checkpoint.get("channel_versions") or {})
    value: Any
    if EXECUTED_CHANNEL in inline:
        value = inline[EXECUTED_CHANNEL]
    else:
        version = versions.get(EXECUTED_CHANNEL)
        blob = next(
            (
                b
                for b in blobs
                if b["channel"] == EXECUTED_CHANNEL
                and str(b["version"]) == str(version)
                and b["checkpoint_ns"] == row["checkpoint_ns"]
            ),
            None,
        )
        if version is None or blob is None or blob["type"] == "empty":
            return None
        import base64

        value = _serializer().loads_typed((blob["type"], base64.b64decode(blob["blob"])))
    if not isinstance(value, list):
        raise _Unreadable(f"the head's {EXECUTED_CHANNEL} channel is not a list")
    return [int(item[1]) for item in cast(list[Any], value)]


def _stale(
    redelivery: Event | None,
    orchestrator: Sequence[Event],
    histories: Mapping[str, Sequence[Event]],
    rows: Sequence[Mapping[str, Any]],
    writes: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if redelivery is None:
        return None
    old, op = str(redelivery.data.get("from")), str(redelivery.op_id)
    events = histories.get(old, ())
    released = {
        hit
        for e in orchestrator
        if e.kind == "fp.release"
        and e.seq > redelivery.seq
        and e.data.get("proc") == old
        and isinstance(hit := e.data.get("hit_seq"), int)
    }
    resumed = [
        e
        for e in events
        if e.kind == "fp.action"
        and isinstance(hit := e.data.get("hit_seq"), int)
        and hit in released
    ]
    stale: dict[str, Any] = {"worker": old, "op": op, "resumed": bool(resumed)}
    if not resumed:
        stale.update(
            attempted={"checkpoints": 0, "writes": 0},
            durable={"checkpoints": 0, "writes": 0},
            fenced={"checkpoints": 0, "writes": 0},
            other_errors=0,
        )
        return stale
    cut = min(e.seq for e in resumed)
    ended = [
        e
        for e in events
        if e.op_id == op
        and e.seq > cut
        and e.kind
        in {
            "saver.put.return",
            "saver.put.raise",
            "saver.put_writes.return",
            "saver.put_writes.raise",
        }
    ]
    checkpoint_ids = {str(r["checkpoint_id"]) for r in rows}
    write_keys = {(str(w["checkpoint_id"]), str(w["task_id"])) for w in writes}
    # Task writes another run acknowledged for the same (checkpoint, task): the stale run's
    # ON CONFLICT DO NOTHING left no row of its own there.
    others = {
        (e.checkpoint_id, e.task_id)
        for proc, evs in histories.items()
        for e in evs
        if e.kind == "saver.put_writes.return"
        and e.status == "ok"
        and (proc != old or e.op_id != op or e.seq <= cut)
    }
    attempted: Counter[str] = Counter()
    durable: Counter[str] = Counter()
    fenced: Counter[str] = Counter()
    other_errors = 0
    acknowledged_not_durable: list[str] = []
    for e in ended:
        table = "checkpoints" if e.kind.startswith("saver.put.") else "writes"
        attempted[table] += 1
        if e.kind.endswith(".raise"):
            if is_fence_error(e.error):
                fenced[table] += 1
            else:
                other_errors += 1
        elif e.status == "ok":
            if table == "checkpoints":
                if e.checkpoint_id in checkpoint_ids:
                    durable[table] += 1
                else:
                    acknowledged_not_durable.append(f"put {e.checkpoint_id}")
            elif (e.checkpoint_id, e.task_id) in others:
                pass  # no row of the stale run's: the other run's row was there first
            elif (str(e.checkpoint_id), str(e.task_id)) in write_keys:
                durable[table] += 1
            else:
                acknowledged_not_durable.append(f"put_writes {e.checkpoint_id}/{e.task_id}")
    stale.update(
        attempted={"checkpoints": attempted["checkpoints"], "writes": attempted["writes"]},
        durable={"checkpoints": durable["checkpoints"], "writes": durable["writes"]},
        fenced={"checkpoints": fenced["checkpoints"], "writes": fenced["writes"]},
        other_errors=other_errors,
        every_attempt_fenced=sum(attempted.values()) > 0
        and sum(fenced.values()) == sum(attempted.values()),
        acknowledged_not_durable=acknowledged_not_durable,
    )
    return stale


def _effects(
    directory: Path,
    histories: Mapping[str, Sequence[Event]],
    acknowledged: Run | None,
    head_attempts: list[int] | None,
) -> list[dict[str, Any]]:
    export = _json(directory / "ledger.json")
    if export.get("verify_ok") is not True:
        raise _Unreadable("ledger.json is not an export the sink verified")
    requests = [e for events in histories.values() for e in events if e.kind == "effect.request"]
    logical_ops = sorted(
        {str(e.logical_op) for e in requests} | {str(r["logical_op"]) for r in export["rows"]}
    )
    found: list[dict[str, Any]] = []
    for logical_op in logical_ops:
        mine = [r for r in export["rows"] if str(r["logical_op"]) == logical_op]
        applied = sorted(int(r["attempt"]) for r in mine if r["status"] == "applied")
        duplicates = sorted(int(r["attempt"]) for r in mine if r["status"] == "duplicate")
        other = [str(r["status"]) for r in mine if r["status"] not in ("applied", "duplicate")]
        requested = sorted(int(e.attempt or 0) for e in requests if str(e.logical_op) == logical_op)
        if not requested and not mine:
            logical = "NONE"
        elif len(applied) == 1:
            logical = "EXACTLY_ONCE"
        elif len(applied) > 1:
            logical = "DUPLICATED"
        elif acknowledged is not None:
            logical = "LOST"
        else:
            logical = "NONE"
        if len(applied) == 1 and head_attempts is not None:
            consistency = "CONSISTENT" if applied == head_attempts else "INCONSISTENT"
        else:
            consistency = "NOT_APPLICABLE"
        found.append(
            {
                "logical_op": logical_op,
                "requested": len(requested),
                "attempts_requested": requested,
                "applied": len(applied),
                "attempts_applied": applied,
                "duplicates": len(duplicates),
                "attempts_duplicate": duplicates,
                "other_statuses": other,
                "logical": logical,
                "attempt_consistency": consistency,
            }
        )
    return found


def _fence(
    directory: Path, thread: str, orchestrator: Sequence[Event], acknowledged: Run | None
) -> dict[str, Any] | None:
    path = directory / OWNERS_FILE
    if not path.exists():
        return None
    owners = _json(path)
    owner = owners.get("owners", {}).get(thread)
    issued = None
    if acknowledged is not None:
        worker, op = acknowledged
        issued = next(
            (
                e.data.get("epoch")
                for e in reversed(orchestrator)
                if e.kind in {"dispatch.send", "dispatch.redeliver"}
                and e.op_id == op
                and e.data.get("to") == worker
            ),
            None,
        )
    return {
        "mode": owners.get("mode"),
        "owner_epoch": owner,
        "acknowledged_epoch": issued,
        "consistent": owner is not None and owner == issued,
    }


# -- a batch ------------------------------------------------------------------------------------


def _variant(batch_dir: Path) -> tuple[str | None, bool]:
    scenario = _json(batch_dir / "scenario.json")
    variant = cast(dict[str, Any], scenario.get("variant") or {})
    fence = variant.get("fence")
    return (str(fence) if fence is not None else None), bool(
        variant.get("idempotent_effects", False)
    )


def trial_directories(batch_dir: Path) -> list[Path]:
    return sorted(p.parent for p in batch_dir.rglob("trial.json"))


def evaluate_batch(batch_dir: Path) -> list[dict[str, Any]]:
    """Every trial's record, in trial order; nothing is written."""
    fence, idempotent = _variant(batch_dir)
    return [
        evaluate_trial(d, fence=fence, idempotent_effects=idempotent)
        for d in trial_directories(batch_dir)
    ]


class NotWritten(Exception):
    """A record already on disk differs from the one computed now."""


def write_records(
    batch_dir: Path, repo: Path | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Write each trial's contract.json and the batch's mitigation.json; a file already there
    must be byte-identical to what is computed now, or nothing more is written. With `repo`,
    the summary carries the judgement against the cell's pre-registered contract prediction
    (crashcourt.mitigation.judge), when the batch's cell has one."""
    fence, idempotent = _variant(batch_dir)
    records: list[dict[str, Any]] = []
    for directory in trial_directories(batch_dir):
        record = evaluate_trial(directory, fence=fence, idempotent_effects=idempotent)
        text = json.dumps(record, indent=2, sort_keys=True) + "\n"
        path = directory / CONTRACT_FILE
        if path.exists():
            if path.read_text(encoding="utf-8") != text:
                raise NotWritten(f"{path} differs from the record computed now")
        else:
            path.write_text(text, encoding="utf-8")
        records.append(record)
    summary = summarize(batch_dir, records)
    if repo is not None:
        from crashcourt.mitigation.judge import judge, predicted_for

        cell = predicted_for(batch_dir, repo)
        if cell is not None and cell.contract is not None:
            summary["judgement"] = judge(records, cell.contract, deterministic=cell.deterministic)
    text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    path = batch_dir / SUMMARY_FILE
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise NotWritten(f"{path} differs from the summary computed now")
    else:
        path.write_text(text, encoding="utf-8")
    return records, summary


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    """The Wilson score interval of k successes in n trials, at 95%."""
    if n == 0:
        return None
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denominator
    low = 0.0 if k == 0 else max(0.0, center - half)
    high = 1.0 if k == n else min(1.0, center + half)
    return (low, high)


def _share(k: int, n: int) -> dict[str, Any]:
    interval = wilson(k, n)
    return {"count": k, "of": n, "wilson95": list(interval) if interval else None}


def summarize(batch_dir: Path, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts over the readable trials of one batch, each with its interval."""
    readable = [r for r in records if not r["problems"]]
    n = len(readable)
    batch = _json(batch_dir / "batch.json")
    scenario = _json(batch_dir / "scenario.json")
    fence, idempotent = _variant(batch_dir)
    stale = [r["stale"] for r in readable if r.get("stale") is not None]
    effects = [e for r in readable for e in r["effects"]]
    return {
        "v": 1,
        "contract": CONTRACT,
        "batch": batch["batch"],
        "cell": scenario["cell"],
        "scenario_sha256": batch["scenario_sha256"],
        "variant": {"fence": fence, "idempotent_effects": idempotent},
        "trials": len(records),
        "readable": n,
        "unreadable": [
            {"trial": r.get("trial"), "problems": r["problems"]} for r in records if r["problems"]
        ],
        "fork": _share(sum(1 for r in readable if r["checkpoints"]["fork"]), n),
        "acknowledged_is_head": _share(
            sum(1 for r in readable if r["checkpoints"].get("acknowledged_is_head")), n
        ),
        "head_attempts": dict(
            Counter(json.dumps(r["checkpoints"].get("head_attempts")) for r in readable)
        ),
        "stale": {
            "trials": len(stale),
            "resumed": _share(sum(1 for s in stale if s["resumed"]), len(stale)),
            "attempted": dict(Counter(json.dumps(s["attempted"], sort_keys=True) for s in stale)),
            "none_durable": _share(
                sum(1 for s in stale if not any(s["durable"].values())), len(stale)
            ),
            "every_attempt_fenced": _share(
                sum(1 for s in stale if s.get("every_attempt_fenced")), len(stale)
            ),
            "other_errors": sum(s["other_errors"] for s in stale),
            "acknowledged_not_durable": sum(
                len(s.get("acknowledged_not_durable", [])) for s in stale
            ),
        },
        "logical": dict(Counter(r["logical"] for r in readable)),
        "effects": {
            "logical_ops_per_trial": dict(Counter(len(r["effects"]) for r in readable)),
            "requested": dict(Counter(e["requested"] for e in effects)),
            "applied": dict(Counter(e["applied"] for e in effects)),
            "duplicates": dict(Counter(e["duplicates"] for e in effects)),
            "attempt_consistency": dict(Counter(e["attempt_consistency"] for e in effects)),
        },
        "fence": {
            "trials": sum(1 for r in readable if r.get("fence") is not None),
            "consistent": _share(
                sum(
                    1
                    for r in readable
                    if cast(dict[str, Any], r.get("fence") or {}).get("consistent")
                ),
                sum(1 for r in readable if r.get("fence") is not None),
            ),
        },
        "v1": {
            invariant: dict(counts) for invariant, counts in sorted(_v1_counts(readable).items())
        },
    }


def _v1_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = {}
    for r in records:
        for invariant, outcome in cast(dict[str, str], r.get("v1") or {}).items():
            counts.setdefault(invariant, Counter())[outcome] += 1
    return counts
