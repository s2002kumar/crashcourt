"""The lineage DAG per (thread_id, checkpoint_ns), from raw snapshot rows only.

Nodes are `checkpoints` rows and edges are `parent_checkpoint_id`. The head is the row with the
largest checkpoint_id, compared as the saver's SQL compares it (`ORDER BY checkpoint_id DESC`):
Python string order, which an integration test checks agrees with the column's collation on uuid6
text. Commit order between rows is defined only where commit timestamps differ.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from crashcourt.checker.artifacts import BlobRow, CheckpointRow, Snapshot, WriteRow
from crashcourt.checker.decode import decode

LineageKey = tuple[str, str]


@dataclass(frozen=True)
class Lineage:
    thread_id: str
    checkpoint_ns: str
    rows: Mapping[str, CheckpointRow]
    children: Mapping[str, tuple[str, ...]]
    #: Rows with no parent.
    roots: tuple[str, ...]
    #: (child, missing parent) for every parent id with no row.
    dangling: tuple[tuple[str, str], ...]
    #: (parent, children) for every row with two or more children.
    forks: tuple[tuple[str, tuple[str, ...]], ...]
    head: str | None
    #: Writes by checkpoint id.
    writes: Mapping[str, tuple[WriteRow, ...]] = field(
        default_factory=dict[str, tuple[WriteRow, ...]]
    )

    def ancestors(self, checkpoint_id: str) -> tuple[str, ...]:
        """checkpoint_id and its ancestors, nearest first, stopping at a root or a missing row."""
        path: list[str] = []
        current: str | None = checkpoint_id
        while current is not None and current in self.rows and current not in path:
            path.append(current)
            current = self.rows[current].parent_checkpoint_id
        return tuple(path)

    def head_path(self) -> tuple[str, ...]:
        return self.ancestors(self.head) if self.head is not None else ()


def build_lineages(snapshot: Snapshot) -> dict[LineageKey, Lineage]:
    grouped: dict[LineageKey, dict[str, CheckpointRow]] = defaultdict(dict)
    for row in snapshot.checkpoints:
        grouped[(row.thread_id, row.checkpoint_ns)][row.checkpoint_id] = row
    writes: dict[LineageKey, dict[str, list[WriteRow]]] = defaultdict(lambda: defaultdict(list))
    for write in snapshot.writes:
        writes[(write.thread_id, write.checkpoint_ns)][write.checkpoint_id].append(write)
    lineages: dict[LineageKey, Lineage] = {}
    for key in sorted(grouped):
        rows = dict(sorted(grouped[key].items()))
        children: dict[str, list[str]] = defaultdict(list)
        roots: list[str] = []
        dangling: list[tuple[str, str]] = []
        for checkpoint_id, row in rows.items():
            parent = row.parent_checkpoint_id
            if parent is None:
                roots.append(checkpoint_id)
            elif parent not in rows:
                dangling.append((checkpoint_id, parent))
            else:
                children[parent].append(checkpoint_id)
        lineages[key] = Lineage(
            thread_id=key[0],
            checkpoint_ns=key[1],
            rows=rows,
            children={p: tuple(sorted(c)) for p, c in sorted(children.items())},
            roots=tuple(roots),
            dangling=tuple(dangling),
            forks=tuple((p, tuple(sorted(c))) for p, c in sorted(children.items()) if len(c) > 1),
            head=max(rows) if rows else None,
            writes={c: tuple(w) for c, w in sorted(writes[key].items())},
        )
    return lineages


class ChannelReader:
    """Reads a checkpoint's channel value: inline from the checkpoint column, else its blob."""

    def __init__(self, snapshot: Snapshot) -> None:
        self._blobs: dict[tuple[str, str, str, str], BlobRow] = {
            (b.thread_id, b.checkpoint_ns, b.channel, b.version): b for b in snapshot.blobs
        }

    def blob(self, row: CheckpointRow, channel: str) -> BlobRow | None:
        version = _versions(row).get(channel)
        if version is None:
            return None
        return self._blobs.get((row.thread_id, row.checkpoint_ns, channel, str(version)))

    def value(self, row: CheckpointRow, channel: str) -> tuple[bool, Any]:
        """(present, value). Raises DecodeError if a stored value fails strict decoding."""
        inline = row.checkpoint.get("channel_values")
        if isinstance(inline, dict) and channel in inline:
            return True, inline[channel]
        blob = self.blob(row, channel)
        if blob is None or blob.type == "empty":
            return False, None
        return True, decode(blob.type, blob.blob)

    def inline(self, row: CheckpointRow, channel: str) -> bool:
        inline = row.checkpoint.get("channel_values")
        return isinstance(inline, dict) and channel in inline


def _versions(row: CheckpointRow) -> dict[str, Any]:
    versions = row.checkpoint.get("channel_versions")
    return dict(versions) if isinstance(versions, dict) else {}


def channel_versions(row: CheckpointRow) -> dict[str, str]:
    return {channel: str(version) for channel, version in _versions(row).items()}
