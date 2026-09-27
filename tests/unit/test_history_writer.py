from __future__ import annotations

import itertools
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from crashcourt.history.reader import HistoryError, read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity

RUN = RunIdentity(batch="DEMO-b1", cell="DEMO-cell", trial=3, seed="s")


def test_events_get_contiguous_seq_and_are_readable(tmp_path: Path) -> None:
    path = tmp_path / "worker-1.jsonl"
    with HistoryWriter(path, run=RUN, proc="worker-1") as writer:
        a = writer.emit("node.start", thread_id="t1", attempt=1)
        b = writer.emit("saver.put.call", thread_id="t1", fp="put.before")
    assert (a.seq, b.seq) == (0, 1)
    assert a.mono_ns <= b.mono_ns
    result = read_history(path)
    assert result.events == (a, b)
    assert result.partial_tail == b""
    assert {e.batch for e in result.events} == {"DEMO-b1"}


def test_each_event_is_on_disk_when_emit_returns(tmp_path: Path) -> None:
    path = tmp_path / "worker-1.jsonl"
    with HistoryWriter(path, run=RUN, proc="worker-1") as writer:
        event = writer.emit("node.start")
        assert path.read_bytes() == event.to_line().encode()


def test_lines_end_with_lf_only(tmp_path: Path) -> None:
    path = tmp_path / "worker-1.jsonl"
    with HistoryWriter(path, run=RUN, proc="worker-1") as writer:
        writer.emit("node.start")
    assert b"\r" not in path.read_bytes()


def test_existing_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "worker-1.jsonl"
    path.write_bytes(b"")
    with pytest.raises(FileExistsError):
        HistoryWriter(path, run=RUN, proc="worker-1")


def test_invalid_event_writes_nothing_and_keeps_seq(tmp_path: Path) -> None:
    path = tmp_path / "worker-1.jsonl"
    with HistoryWriter(path, run=RUN, proc="worker-1") as writer:
        with pytest.raises(ValidationError):
            writer.emit("fault.inject")  # fault_id is required
        event = writer.emit("fault.inject", fault_id="f-1")
    assert event.seq == 0
    assert read_history(path).events == (event,)


def test_concurrent_emitters_get_unique_contiguous_seq(tmp_path: Path) -> None:
    path = tmp_path / "worker-1.jsonl"
    with HistoryWriter(path, run=RUN, proc="worker-1") as writer:

        def burst() -> None:
            for _ in range(200):
                writer.emit("saver.put_writes.call")

        threads = [threading.Thread(target=burst) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    events = read_history(path).events
    assert [e.seq for e in events] == list(range(1600))
    assert all(a.mono_ns <= b.mono_ns for a, b in itertools.pairwise(events))


def test_partial_final_line_is_reported_not_parsed(tmp_path: Path) -> None:
    path = tmp_path / "worker-1.jsonl"
    with HistoryWriter(path, run=RUN, proc="worker-1") as writer:
        event = writer.emit("node.start")
    with path.open("ab") as handle:
        handle.write(b'{"v":1,"batch":"DEMO-b1"')
    result = read_history(path)
    assert result.events == (event,)
    assert result.partial_tail == b'{"v":1,"batch":"DEMO-b1"'


def test_unparseable_complete_line_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "worker-1.jsonl"
    with HistoryWriter(path, run=RUN, proc="worker-1") as writer:
        writer.emit("node.start")
    with path.open("ab") as handle:
        handle.write(b"not json\n")
    with pytest.raises(HistoryError, match="line 2"):
        read_history(path)


def test_closed_writer_refuses_events(tmp_path: Path) -> None:
    writer = HistoryWriter(tmp_path / "w.jsonl", run=RUN, proc="w")
    writer.close()
    with pytest.raises(ValueError, match="closed"):
        writer.emit("node.start")
