"""The effect sink's HTTP API (FastAPI), served by uvicorn:

    uvicorn --factory crashcourt.sink.app:from_env --host 127.0.0.1 --port 8480

| Endpoint | Does |
| --- | --- |
| GET /health | liveness, version, source hash, pid, clock, open trial, and any test hook |
| POST /trials | open, or reopen after a restart, one trial's ledger (`apply_once`: idempotent) |
| POST /effects | append one effect; answers only after the commit, saying whether it was applied |
| POST /attempts/next | increment and return an attempt counter |
| GET /ledger | every row of the trial's ledger |
| GET /ledger/verify | recompute the trial's hash chain |

Every request but /health and POST /trials names the trial it is for (in the body, or as batch,
cell, trial, and seed query parameters) and gets 409 unless that trial is open, so a late request
from a previous trial's worker cannot land in the next trial's ledger, and a reader cannot take one
trial's rows for another's. POST /trials answers 409 for a ledger file that belongs to another
trial or ledger version, and leaves the open trial as it was.

/health reports the sink's monotonic clock (`mono_ns`). The ledger stamps rows with it, and the
merge breaks ties with it, so it is comparable with the workers' clocks only when the sink and the
workers share a kernel: not between a host and a Docker Desktop VM (Windows, macOS). Doctor shows
the offset; nothing here fails on it.

`CRASHCOURT_SINK_DATA` is the ledger directory. `CRASHCOURT_SINK_TEST_EXIT_AFTER_COMMIT=N` is a
test hook only: the process exits right after committing its Nth effect, before answering, so a
test can prove the commit precedes the response. `/health` reports it, and doctor refuses a sink
that has it armed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from crashcourt import __version__
from crashcourt.history.writer import host_monotonic_ns
from crashcourt.sink.ledger import LEDGER_VERSION, EffectRequest, Ledger, LedgerConflict, TrialId
from crashcourt.sink.source import build_root, source_sha256

EXIT_HOOK_ENV = "CRASHCOURT_SINK_TEST_EXIT_AFTER_COMMIT"


#: A batch or cell id: it names a directory, so no separators, no leading dot, no "..".
PathId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


class TrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch: PathId
    cell: PathId
    trial: int = Field(ge=0)
    seed: str


class OpenRequest(TrialRequest):
    """POST /trials: the trial, and whether its ledger applies at most one effect per logical
    op (crashcourt.sink.ledger); a trial reopened after a restart keeps the mode it was opened
    with, and asking for the other is refused."""

    apply_once: bool = False


class AttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trial: TrialRequest
    key: str = Field(min_length=1)


class EffectPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trial: TrialRequest
    effect: EffectRequest


def _write_atomically(path: Path, data: bytes) -> None:
    """Write a file so that a crash leaves the old content or the new, never a mix."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if sys.platform != "win32":  # POSIX: make the rename itself durable
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class SinkState:
    """The open ledger, persisted as `current.json` so a restarted sink resumes the same trial."""

    def __init__(self, root: Path, exit_after_commit: int | None) -> None:
        self.root = root
        self.exit_after_commit = exit_after_commit
        self.commits = 0
        self.ledger: Ledger | None = None
        #: Why the trial in current.json could not be resumed at start, if it could not.
        self.resume_error: str | None = None
        self._lock = threading.RLock()
        root.mkdir(parents=True, exist_ok=True)
        current = root / "current.json"
        if current.exists():
            try:
                recorded = json.loads(current.read_text(encoding="utf-8"))
                apply_once = bool(recorded.pop("apply_once", False))
                self.open(TrialId(**recorded), apply_once=apply_once)
            except HTTPException as exc:  # a file from another ledger version or trial
                self.resume_error = str(exc.detail)

    def open(self, trial: TrialId, *, apply_once: bool = False) -> Ledger:
        with self._lock:
            if self.ledger is not None and self.ledger.trial == trial:
                if self.ledger.apply_once != apply_once:
                    raise HTTPException(
                        status_code=409,
                        detail=f"trial {trial.__dict__} is open "
                        f"{'with' if self.ledger.apply_once else 'without'} apply_once",
                    )
                return self.ledger
            # Open the new ledger before closing the old one, so a refused file leaves the open
            # trial working.
            try:
                ledger = Ledger(trial.path(self.root), trial, apply_once=apply_once)
            except LedgerConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            data = json.dumps(
                {**trial.__dict__, **({"apply_once": True} if apply_once else {})}, sort_keys=True
            ).encode("utf-8")
            try:
                _write_atomically(self.root / "current.json", data)
            except BaseException:
                ledger.close()
                raise
            if self.ledger is not None:
                self.ledger.close()
            self.ledger = ledger
            self.resume_error = None
            return ledger

    def open_status(self, trial: TrialId, *, apply_once: bool) -> dict[str, Any]:
        """Open and report a trial without exposing a ledger that another opener can close."""
        with self._lock:
            ledger = self.open(trial, apply_once=apply_once)
            return {
                "trial": ledger.trial.__dict__,
                "rows": len(ledger.rows()),
                "apply_once": ledger.apply_once,
            }

    @contextmanager
    def access(self, trial: TrialRequest) -> Iterator[Ledger]:
        """Keep a validated ledger open through its operation, including while switching trials."""
        with self._lock:
            ledger = self.ledger
            if ledger is None:
                raise HTTPException(status_code=409, detail="no trial is open; POST /trials first")
            if TrialId(**trial.model_dump()) != ledger.trial:
                raise HTTPException(
                    status_code=409,
                    detail=f"request for trial {trial.model_dump()}, "
                    f"but the open trial is {ledger.trial.__dict__}",
                )
            yield ledger


def create_app(
    root: Path, *, exit_after_commit: int | None = None, source_root: Path | None = None
) -> FastAPI:
    state = SinkState(root, exit_after_commit)
    source = source_sha256(source_root)
    app = FastAPI(title="Crashcourt effect sink", version=__version__)

    @app.get("/health")
    def health() -> dict[str, Any]:
        trial = state.ledger.trial.__dict__ if state.ledger is not None else None
        return {
            "status": "ok",
            "version": __version__,
            "source_sha256": source,
            "pid": os.getpid(),
            "mono_ns": host_monotonic_ns(),
            "ledger_version": LEDGER_VERSION,
            "trial": trial,
            "apply_once": state.ledger.apply_once if state.ledger is not None else None,
            "resume_error": state.resume_error,
            "test_hooks": state.exit_after_commit is not None,
        }

    @app.post("/trials")
    def open_trial(request: OpenRequest) -> dict[str, Any]:
        return state.open_status(
            TrialId(**request.model_dump(exclude={"apply_once"})), apply_once=request.apply_once
        )

    @app.post("/effects")
    def append_effect(request: EffectPost) -> dict[str, Any]:
        with state.access(request.trial) as ledger:
            row = ledger.append(request.effect)
            state.commits += 1
            if state.exit_after_commit is not None and state.commits >= state.exit_after_commit:
                os._exit(70)  # test hook: committed, never answered
        return {
            "sink_seq": row["sink_seq"],
            "received_mono_ns": row["received_mono_ns"],
            "row_hash": row["row_hash"],
            "status": row["status"],
            "applied": row["applied"],
            "duplicate_of": row["duplicate_of"],
        }

    @app.post("/attempts/next")
    def next_attempt(request: AttemptRequest) -> dict[str, int]:
        with state.access(request.trial) as ledger:
            return {"attempt": ledger.next_attempt(request.key)}

    @app.get("/ledger")
    def ledger_rows(trial: Annotated[TrialRequest, Query()]) -> dict[str, Any]:
        with state.access(trial) as ledger:
            return {"trial": ledger.trial.__dict__, "rows": ledger.rows()}

    @app.get("/ledger/verify")
    def verify(trial: Annotated[TrialRequest, Query()]) -> dict[str, Any]:
        with state.access(trial) as ledger:
            result = ledger.verify()
            return {
                "trial": ledger.trial.__dict__,
                "ok": result.ok,
                "rows": result.rows,
                "first_bad": result.first_bad,
            }

    return app


def from_env() -> FastAPI:
    hook = os.environ.get(EXIT_HOOK_ENV)
    root = Path(os.environ.get("CRASHCOURT_SINK_DATA", "sink-data"))
    return create_app(root, exit_after_commit=int(hook) if hook else None, source_root=build_root())
