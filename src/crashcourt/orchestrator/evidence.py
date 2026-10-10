"""The post-trial evidence the checker reads beside the trial's histories: ledger.json,
snapshot.json, and sink-health.json.

Their formats are the frozen checker's (crashcourt.checker.load). Each file is the model's JSON,
built by the checker's own helpers, so its bytes are that schema by construction:

| File | Built by |
| --- | --- |
| ledger.json | `LedgerExport.from_sink`: the sink's GET /ledger rows and GET /ledger/verify |
| sink-health.json | `SinkHealth.from_reading`: GET /health before the trial, then after it |
| snapshot.json | `crashcourt.snapshot.dump.take_pair` on the direct DSN, with `pretrial_rows` |

They are evidence only, and nothing read for them decides the schedule, a fault, or a retry. The
"before" health reading and the pre-trial row counts are taken after the sink opened the trial
and before the first dispatch, and are only recorded: a failure there is reported once the trial
is over, never by holding the trial back. Everything else is read after quiescence, when every
worker the trial started has exited and been reaped and the orchestrator's history is closed.
The snapshot reads the target database on the direct DSN, the oracle's path, never a worker's
Toxiproxy route.

Missing or malformed evidence fails closed. A read that fails, or an answer the checker's helpers
refuse, leaves that file absent, never partial, and the trial says why. The checker then makes
the trial INVALID.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter

from crashcourt.checker.artifacts import LedgerExport, SinkHealth
from crashcourt.faults.toxiproxy import ToxiproxyClient
from crashcourt.snapshot import dump

LEDGER_FILE = "ledger.json"
SNAPSHOT_FILE = "snapshot.json"
HEALTH_FILE = "sink-health.json"

_HEALTH = TypeAdapter(tuple[SinkHealth, ...])


class HealthReader(Protocol):
    def health(self) -> dict[str, Any]: ...


@dataclass
class Before:
    """What was read after the sink opened the trial and before the first dispatch."""

    health: Mapping[str, Any] | None = None
    pretrial: dict[str, int] | None = None
    problems: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class SinkAfter:
    """The sink's answers in the post-trial step, all three from one read."""

    health: Mapping[str, Any]
    rows: Sequence[Mapping[str, Any]]
    verify: Mapping[str, Any]


def read_before(sink: HealthReader, dsn: str, threads: Sequence[str]) -> Before:
    """The "before" reading and the pre-trial row counts, each recorded or its failure noted."""
    before = Before()
    try:
        before.health = sink.health()
    except Exception as exc:  # noqa: BLE001 - an unread sink is absent evidence, not a crash
        before.problems.append(f"{HEALTH_FILE}: the sink could not be read before the trial: {exc}")
    try:
        before.pretrial = dump.pretrial_rows(dsn, threads)
    except Exception as exc:  # noqa: BLE001 - as above, for the database
        before.problems.append(
            f"{SNAPSHOT_FILE}: the pre-trial row counts could not be read: {exc}"
        )
    return before


def toxics_installed(client: ToxiproxyClient) -> list[str]:
    """Every toxic on any proxy, as `proxy/toxic`, sorted."""
    return sorted(
        f"{proxy.name}/{toxic.name}" for proxy in client.proxies() for toxic in proxy.toxics
    )


def write_after(
    directory: Path,
    before: Before,
    after: SinkAfter | None,
    *,
    dsn: str,
    threads: Sequence[str],
    toxiproxy: ToxiproxyClient,
    after_orchestrator_seq: int | None,
    quiescent: bool,
) -> list[str]:
    """Write the three files after quiescence; why each one that is absent is absent."""
    problems = list(before.problems)
    problems.extend(_ledger(directory, after))
    problems.extend(_health(directory, before, after))
    problems.extend(
        _snapshot(
            directory,
            before,
            dsn=dsn,
            threads=threads,
            toxiproxy=toxiproxy,
            after_orchestrator_seq=after_orchestrator_seq,
            quiescent=quiescent,
        )
    )
    return problems


def _ledger(directory: Path, after: SinkAfter | None) -> list[str]:
    if after is None:
        return [f"{LEDGER_FILE} not written: the sink could not be read after the trial"]
    try:
        export = LedgerExport.from_sink(after.rows, after.verify)
    except ValueError as exc:
        return [f"{LEDGER_FILE} not written: {exc}"]
    _write(directory / LEDGER_FILE, export.model_dump_json().encode("utf-8"))
    return []


def _health(directory: Path, before: Before, after: SinkAfter | None) -> list[str]:
    if before.health is None or after is None:
        return [f"{HEALTH_FILE} not written: a reading is missing"]
    try:
        readings = (
            SinkHealth.from_reading(before.health, "before"),
            SinkHealth.from_reading(after.health, "after"),
        )
    except ValueError as exc:
        return [f"{HEALTH_FILE} not written: {exc}"]
    _write(directory / HEALTH_FILE, _HEALTH.dump_json(readings))
    return []


def _snapshot(
    directory: Path,
    before: Before,
    *,
    dsn: str,
    threads: Sequence[str],
    toxiproxy: ToxiproxyClient,
    after_orchestrator_seq: int | None,
    quiescent: bool,
) -> list[str]:
    if before.pretrial is None:
        return [f"{SNAPSHOT_FILE} not written: the pre-trial row counts are missing"]
    if not quiescent:
        return [f"{SNAPSHOT_FILE} not written: a worker the trial started was not reaped"]
    if after_orchestrator_seq is None:
        return [f"{SNAPSHOT_FILE} not written: the orchestrator recorded no event"]
    try:
        toxics = toxics_installed(toxiproxy)
        pair = dump.take_pair(
            dsn,
            threads,
            before.pretrial,
            after_orchestrator_seq=after_orchestrator_seq,
            toxics_installed=toxics,
        )
    except Exception as exc:  # noqa: BLE001 - no snapshot rather than a partial one
        return [f"{SNAPSHOT_FILE} not written: {exc}"]
    _write(directory / SNAPSHOT_FILE, pair.model_dump_json().encode("utf-8"))
    return []


def _write(path: Path, data: bytes) -> None:
    """Write through a temporary name, so the file is whole or absent."""
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(data)
    os.replace(temporary, path)
