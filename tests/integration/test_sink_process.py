"""The sink as a real process: it commits before it answers, and a restart keeps the ledger."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity
from crashcourt.sink.app import EXIT_HOOK_ENV
from crashcourt.sink.client import SinkClient, SinkError, record_effect
from crashcourt.sink.ledger import EffectRequest, TrialId

pytestmark = pytest.mark.integration

TRIAL = TrialId(batch="DEMO-b", cell="DEMO-c", trial=1, seed="s")
EFFECT = EffectRequest(
    effect_key="charge:order-17",
    logical_op="approve:t3:i1",
    op_id="op-7",
    attempt=1,
    worker="worker-1",
    pid=os.getpid(),  # the history writer's pid: record_effect checks they match
    thread_id="t3",
    node="execute",
    checkpoint_id_seen="C4",
    payload_hash=hashlib.sha256(b"charge").hexdigest(),
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def sink_process(root: Path, **env: str) -> Iterator[tuple[subprocess.Popen[bytes], SinkClient]]:
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "--factory",
            "crashcourt.sink.app:from_env",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env={**os.environ, "CRASHCOURT_SINK_DATA": str(root), **env},
    )
    client = SinkClient(f"http://127.0.0.1:{port}", timeout=5)
    deadline = time.monotonic() + 30
    while True:
        try:
            client.health()
            break
        except SinkError:
            if process.poll() is not None or time.monotonic() > deadline:
                process.kill()
                raise
            time.sleep(0.1)
    try:
        yield process, client
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(10)


def test_the_commit_happens_before_the_answer(tmp_path: Path) -> None:
    root = tmp_path / "sink"
    history_path = tmp_path / "worker-1.jsonl"
    with sink_process(root, **{EXIT_HOOK_ENV: "1"}) as (process, client):
        assert client.health()["test_hooks"] is True
        client.open_trial(TRIAL)
        run = RunIdentity(TRIAL.batch, TRIAL.cell, TRIAL.trial, TRIAL.seed)
        with (
            HistoryWriter(history_path, run=run, proc="worker-1") as history,
            pytest.raises(SinkError),
        ):
            record_effect(history, client, EFFECT)
        assert process.wait(10) == 70  # it died after committing, before answering
    with sink_process(root) as (_, client):  # restarted over the same data
        assert client.health()["trial"] == TRIAL.__dict__
        rows = client.ledger()
        assert [(r["effect_key"], r["op_id"], r["attempt"]) for r in rows] == [
            ("charge:order-17", "op-7", 1)
        ]
        assert client.verify() == {"ok": True, "rows": 1, "first_bad": None}
    events = read_history(history_path).events
    assert [(e.kind, e.status) for e in events] == [
        ("effect.request", None),
        ("effect.response", "info"),
    ]


def test_a_restart_keeps_the_ledger_and_the_counters(tmp_path: Path) -> None:
    root = tmp_path / "sink"
    history_path = tmp_path / "worker-1.jsonl"
    run = RunIdentity(TRIAL.batch, TRIAL.cell, TRIAL.trial, TRIAL.seed)
    with HistoryWriter(history_path, run=run, proc="worker-1") as history:
        with sink_process(root) as (_, client):
            client.open_trial(TRIAL)
            assert record_effect(history, client, EFFECT) == 1
            assert client.next_attempt(TRIAL, "t3/execute/0") == 1
        with sink_process(root) as (_, client):
            assert record_effect(history, client, EFFECT) == 2
            assert client.next_attempt(TRIAL, "t3/execute/0") == 2
            assert client.verify()["ok"] is True
    responses = [e for e in read_history(history_path).events if e.kind == "effect.response"]
    assert [e.data["sink_seq"] for e in responses] == [1, 2]
