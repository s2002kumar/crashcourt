"""The control protocol's framing and messages: one JSON object per line, each side its own set."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from crashcourt.spec.protocol import (
    MAX_LINE,
    ArmMessage,
    Closed,
    Hello,
    Hit,
    ProtocolError,
    ReleaseMessage,
    Result,
    Run,
    Shutdown,
    encode,
    parse_from_orchestrator,
    parse_from_worker,
    read_line,
)


def lines_from(chunks: list[bytes], *, eof: bool = True, limit: int = MAX_LINE) -> list[object]:
    """Feed chunks to a stream reader and read lines until it closes; returns lines and the end."""

    async def go() -> list[object]:
        reader = asyncio.StreamReader(limit=limit)
        for chunk in chunks:
            reader.feed_data(chunk)
        if eof:
            reader.feed_eof()
        got: list[object] = []
        while True:
            try:
                got.append(await read_line(reader))
            except (Closed, ProtocolError) as exc:
                got.append(exc)
                return got

    return asyncio.run(go())


WORKER_MESSAGES: list[BaseModel] = [
    Hello(worker="w1", pid=4242),
    Hit(hit_seq=7, fp="get_tuple.after", op_id="op-1", occurrence=1),
    Result(op_id="op-1", status="ok", output={"trace": ["first", "second"]}, worker_seq=12),
    Result(op_id="op-2", status="fail", error="boom", error_type="builtins.ValueError"),
]
ORCHESTRATOR_MESSAGES: list[BaseModel] = [
    ArmMessage(point="put.before", action="pause", op_id="op-1"),
    ArmMessage(point="put.after", action="signal", signal="SIGKILL", occurrence=2),
    Run(op_id="op-1", thread_id="b/c/1/t1", durability="sync", input={"n": 1}),
    ReleaseMessage(hit_seq=7),
    Shutdown(),
]


def test_each_message_is_one_line_and_round_trips() -> None:
    for message in WORKER_MESSAGES:
        line = encode(message)
        assert line.endswith(b"\n") and line.count(b"\n") == 1
        assert parse_from_worker(line[:-1]) == message
    for message in ORCHESTRATOR_MESSAGES:
        assert parse_from_orchestrator(encode(message)[:-1]) == message


def test_a_newline_inside_a_value_is_escaped_not_framed() -> None:
    message = Result(op_id="op-1", status="fail", error="line one\nline two")
    line = encode(message)
    assert line.count(b"\n") == 1
    assert parse_from_worker(line[:-1]) == message


def test_framing_survives_any_split_of_the_stream() -> None:
    stream = b"".join(encode(m) for m in WORKER_MESSAGES)
    for size in (1, 2, 7, 64, len(stream)):
        chunks = [stream[i : i + size] for i in range(0, len(stream), size)]
        got = lines_from(chunks)
        assert [parse_from_worker(line) for line in got[:-1]] == WORKER_MESSAGES  # type: ignore[arg-type]
        assert isinstance(got[-1], Closed) and got[-1].partial == b""


def test_a_line_cut_off_by_the_end_of_the_stream_is_not_a_message() -> None:
    whole = encode(Hello(worker="w1", pid=1))
    cut = encode(Result(op_id="op-1", status="ok"))[:-5]
    got = lines_from([whole, cut])
    assert parse_from_worker(got[0]) == Hello(worker="w1", pid=1)  # type: ignore[arg-type]
    assert isinstance(got[1], Closed) and got[1].partial == cut


def test_a_line_longer_than_the_limit_is_a_violation() -> None:
    got = lines_from([b"x" * 200 + b"\n"], limit=64)
    assert isinstance(got[0], ProtocolError)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (b'{"type":"gossip","worker":"w1"}', "invalid 'gossip' message"),
        (b"not json", "not JSON"),
        (b"\xff\xfe", "not JSON"),
        (b"[1, 2]", "not an object"),
        (b'{"worker":"w1","pid":3}', "invalid None message"),
        (b'{"type":"hello","worker":"w1","pid":3,"extra":1}', "extra"),
        (b'{"type":"hello","worker":"w1","pid":0}', "pid"),
        (b'{"type":"hello","protocol":2,"worker":"w1","pid":3}', "protocol"),
        (b'{"type":"result","op_id":"op-1","status":"ok","error":"x"}', "error"),
        (b'{"type":"result","op_id":"op-1","status":"fail"}', "error"),
        (b'{"type":"hit","hit_seq":1,"fp":"put.during","op_id":null,"occurrence":1}', "fp"),
    ],
)
def test_what_a_worker_may_not_send(line: bytes, message: str) -> None:
    with pytest.raises(ProtocolError, match=message):
        parse_from_worker(line)


def test_each_side_accepts_only_its_own_messages() -> None:
    with pytest.raises(ProtocolError, match="'run'"):
        parse_from_worker(encode(Run(op_id="o", thread_id="t", durability="sync"))[:-1])
    with pytest.raises(ProtocolError, match="'hello'"):
        parse_from_orchestrator(encode(Hello(worker="w1", pid=1))[:-1])
    with pytest.raises(ProtocolError, match="'result'"):
        parse_from_orchestrator(encode(Result(op_id="o", status="ok"))[:-1])


def test_the_wire_form_is_plain_json() -> None:
    line = encode(Hit(hit_seq=3, fp="put.before", op_id=None, occurrence=1))
    assert json.loads(line) == {
        "type": "hit",
        "hit_seq": 3,
        "fp": "put.before",
        "op_id": None,
        "occurrence": 1,
    }
