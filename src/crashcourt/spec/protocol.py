"""The control protocol between the orchestrator and a worker: JSON lines over a Unix socket.

One socket per worker. Each message is one JSON object on one line, UTF-8, ending in a newline.
The orchestrator listens, the worker connects and speaks first.

| Message | From | Meaning |
| --- | --- | --- |
| hello | worker | ready: the worker's name, pid, and protocol version |
| arm | orchestrator | install one failpoint arm (before any run that could reach it) |
| run | orchestrator | execute one client operation |
| hit | worker | a run is parked at a failpoint barrier; `fp.hit` is already in its history |
| release | orchestrator | let the parked run with this hit go on; `fp.release` is already logged |
| result | worker | the outcome of a run: exactly one per run, unless the worker dies |
| shutdown | orchestrator | exit |

A message of an unknown type, a type the sender may not send, an invalid field, or a line that is
not JSON is a protocol violation. A line cut off by the end of the stream is not a message: it is
what a process killed mid-write leaves, and it is reported as such, not parsed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from crashcourt.failpoints.registry import Action, Point, Signal

PROTOCOL_VERSION: Literal[1] = 1
#: The longest line either side accepts, newline included.
MAX_LINE = 1 << 20


class ProtocolError(Exception):
    """The peer broke the protocol; the trial cannot trust it."""


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Hello(_Message):
    type: Literal["hello"] = "hello"
    protocol: Literal[1] = PROTOCOL_VERSION
    worker: str
    pid: int = Field(ge=1, strict=True)


class ArmMessage(_Message):
    type: Literal["arm"] = "arm"
    point: Point
    action: Action
    op_id: str | None = None
    occurrence: int = Field(default=1, ge=1, strict=True)
    signal: Signal | None = None
    exit_code: int | None = None


class Run(_Message):
    type: Literal["run"] = "run"
    op_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    durability: Literal["sync", "async", "exit"]
    input: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


class Hit(_Message):
    type: Literal["hit"] = "hit"
    #: The seq of the worker's fp.hit event: the orchestrator's fp.release names it.
    hit_seq: int = Field(ge=0, strict=True)
    fp: Point
    op_id: str | None
    occurrence: int = Field(ge=1, strict=True)


class ReleaseMessage(_Message):
    type: Literal["release"] = "release"
    hit_seq: int = Field(ge=0, strict=True)


class Result(_Message):
    type: Literal["result"] = "result"
    op_id: str = Field(min_length=1)
    status: Literal["ok", "fail"]
    #: What the run returned, for ok: the workload's summary of the final state.
    output: JsonValue = None
    error: str | None = None
    #: The exception's qualified class name, for fail.
    error_type: str | None = None
    #: The seq of the worker's last event before it sent this result; None if it has none yet.
    worker_seq: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def _error_iff_failed(self) -> Self:
        if (self.status == "fail") != (self.error is not None):
            raise ValueError("a failed result carries an error, and only a failed one")
        if self.status == "ok" and self.error_type is not None:
            raise ValueError("an ok result has no error type")
        return self


class Shutdown(_Message):
    type: Literal["shutdown"] = "shutdown"


WorkerMessage = Annotated[Hello | Hit | Result, Discriminator("type")]
OrchestratorMessage = Annotated[ArmMessage | Run | ReleaseMessage | Shutdown, Discriminator("type")]
_FROM_WORKER: TypeAdapter[Hello | Hit | Result] = TypeAdapter(WorkerMessage)
_FROM_ORCHESTRATOR: TypeAdapter[ArmMessage | Run | ReleaseMessage | Shutdown] = TypeAdapter(
    OrchestratorMessage
)


def encode(message: BaseModel) -> bytes:
    """One message as one line."""
    text = json.dumps(
        message.model_dump(mode="json"),
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8") + b"\n"


def _parse(line: bytes, adapter: TypeAdapter[Any], sender: str) -> Any:
    try:
        data: object = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"{sender} sent a line that is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError(f"{sender} sent {type(data).__name__}, not an object")
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        kind = data.get("type")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ProtocolError(
            f"{sender} sent an invalid {kind!r} message: {where}: {first['msg']}"
        ) from exc


def parse_from_worker(line: bytes) -> Hello | Hit | Result:
    return _parse(line, _FROM_WORKER, "the worker")


def parse_from_orchestrator(line: bytes) -> ArmMessage | Run | ReleaseMessage | Shutdown:
    return _parse(line, _FROM_ORCHESTRATOR, "the orchestrator")


class Closed(Exception):
    """The stream ended. `partial` holds a final line cut off without its newline, if any."""

    def __init__(self, partial: bytes = b"") -> None:
        super().__init__("stream closed" + (" mid-message" if partial else ""))
        self.partial = partial


async def read_line(reader: asyncio.StreamReader) -> bytes:
    """The next complete line, newline stripped. Raises Closed at the end of the stream, and
    ProtocolError for a line longer than the reader's limit."""
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        raise Closed(exc.partial) from None
    except asyncio.LimitOverrunError as exc:
        raise ProtocolError(f"a line longer than {MAX_LINE} bytes ({exc.consumed}+)") from exc
    return line[:-1]
