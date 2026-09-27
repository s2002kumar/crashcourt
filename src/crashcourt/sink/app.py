"""The effect sink's HTTP API (FastAPI), served by uvicorn:

    uvicorn --factory crashcourt.sink.app:from_env --host 127.0.0.1 --port 8480

| Endpoint | Does |
| --- | --- |
| GET /health | liveness, version, the open trial, and whether a test hook is armed |
| POST /trials | open, or reopen after a restart, the ledger for one trial |
| POST /effects | append one effect (never deduplicated); answers only after the commit |
| POST /attempts/next | increment and return an attempt counter |
| GET /ledger | every row of the open trial's ledger |
| GET /ledger/verify | recompute the hash chain |

`CRASHCOURT_SINK_DATA` is the ledger directory. `CRASHCOURT_SINK_TEST_EXIT_AFTER_COMMIT=N` is a
test hook only: the process exits right after committing its Nth effect, before answering, so a
test can prove the commit precedes the response. `/health` reports it, and doctor refuses a sink
that has it armed.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from crashcourt import __version__
from crashcourt.sink.ledger import LEDGER_VERSION, EffectRequest, Ledger, TrialId

EXIT_HOOK_ENV = "CRASHCOURT_SINK_TEST_EXIT_AFTER_COMMIT"


class TrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch: str = Field(min_length=1)
    cell: str = Field(min_length=1)
    trial: int = Field(ge=0)
    seed: str


class AttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)


class SinkState:
    """The open ledger, persisted as `current.json` so a restarted sink resumes the same trial."""

    def __init__(self, root: Path, exit_after_commit: int | None) -> None:
        self.root = root
        self.exit_after_commit = exit_after_commit
        self.commits = 0
        self.ledger: Ledger | None = None
        self._lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)
        current = root / "current.json"
        if current.exists():
            self.open(TrialId(**json.loads(current.read_text(encoding="utf-8"))))

    def open(self, trial: TrialId) -> Ledger:
        with self._lock:
            if self.ledger is not None and self.ledger.trial == trial:
                return self.ledger
            if self.ledger is not None:
                self.ledger.close()
            self.ledger = Ledger(trial.path(self.root), trial)
            data = json.dumps(trial.__dict__, sort_keys=True).encode("utf-8")
            (self.root / "current.json").write_bytes(data)
            return self.ledger

    def require(self) -> Ledger:
        if self.ledger is None:
            raise HTTPException(status_code=409, detail="no trial is open; POST /trials first")
        return self.ledger


def create_app(root: Path, *, exit_after_commit: int | None = None) -> FastAPI:
    state = SinkState(root, exit_after_commit)
    app = FastAPI(title="Crashcourt effect sink", version=__version__)

    @app.get("/health")
    def health() -> dict[str, Any]:
        trial = state.ledger.trial.__dict__ if state.ledger is not None else None
        return {
            "status": "ok",
            "version": __version__,
            "ledger_version": LEDGER_VERSION,
            "trial": trial,
            "test_hooks": state.exit_after_commit is not None,
        }

    @app.post("/trials")
    def open_trial(request: TrialRequest) -> dict[str, Any]:
        ledger = state.open(TrialId(**request.model_dump()))
        return {"trial": ledger.trial.__dict__, "rows": len(ledger.rows())}

    @app.post("/effects")
    def append_effect(request: EffectRequest) -> dict[str, Any]:
        row = state.require().append(request)
        state.commits += 1
        if state.exit_after_commit is not None and state.commits >= state.exit_after_commit:
            os._exit(70)  # test hook: committed, never answered
        return {
            "sink_seq": row["sink_seq"],
            "received_mono_ns": row["received_mono_ns"],
            "row_hash": row["row_hash"],
        }

    @app.post("/attempts/next")
    def next_attempt(request: AttemptRequest) -> dict[str, int]:
        return {"attempt": state.require().next_attempt(request.key)}

    @app.get("/ledger")
    def ledger_rows() -> dict[str, Any]:
        ledger = state.require()
        return {"trial": ledger.trial.__dict__, "rows": ledger.rows()}

    @app.get("/ledger/verify")
    def verify() -> dict[str, Any]:
        result = state.require().verify()
        return {"ok": result.ok, "rows": result.rows, "first_bad": result.first_bad}

    return app


def from_env() -> FastAPI:
    hook = os.environ.get(EXIT_HOOK_ENV)
    root = Path(os.environ.get("CRASHCOURT_SINK_DATA", "sink-data"))
    return create_app(root, exit_after_commit=int(hook) if hook else None)
