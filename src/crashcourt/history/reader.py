"""Read a per-process history file back into events."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from crashcourt.spec.events import Event


class HistoryError(Exception):
    """A history that cannot be trusted as evidence; the trial it belongs to is INVALID."""


@dataclass(frozen=True)
class ReadResult:
    events: tuple[Event, ...]
    #: Bytes after the last newline: the line a killed process was writing, or b"" if none.
    partial_tail: bytes


def read_history(path: Path) -> ReadResult:
    """Parse every complete line. Only an unterminated final line may be incomplete."""
    *complete, tail = path.read_bytes().split(b"\n")
    events: list[Event] = []
    for number, line in enumerate(complete, start=1):
        try:
            events.append(Event.model_validate_json(line))
        except ValidationError as exc:
            raise HistoryError(f"{path}: line {number} is not a valid event: {exc}") from exc
    return ReadResult(tuple(events), tail)
