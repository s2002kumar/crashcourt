"""A worker's client for the effect sink, and the effect events it records around each call.

`record_effect` writes `effect.request` before the POST and `effect.response` after it, carrying
the sink's `sink_seq`, so the merger can order request, `sink.append`, and response (T-04).
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast

from crashcourt.history.writer import HistoryWriter
from crashcourt.sink.ledger import EffectRequest, TrialId

DEFAULT_URL = "http://127.0.0.1:8480"


class SinkError(Exception):
    """The sink did not confirm the request.

    `status` is the HTTP status when the sink answered with an error. A 4xx answer means the sink
    refused the request, so it did not apply it; with no answer (refused, reset, timed out, cut off
    mid-body), the outcome is unknown and only the ledger can say.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def refused(self) -> bool:
        return self.status is not None and 400 <= self.status < 500


class SinkClient:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: object | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.url + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return cast(dict[str, Any], json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise SinkError(f"{method} {path}: HTTP {exc.code} {detail}", exc.code) from exc
        # Refused, reset, timed out, or cut off mid-answer: the outcome is unknown.
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise SinkError(f"{method} {path}: {type(exc).__name__}: {exc}") from exc

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health")

    def open_trial(self, trial: TrialId) -> dict[str, Any]:
        return self._call("POST", "/trials", trial.__dict__)

    def post_effect(self, trial: TrialId, effect: EffectRequest) -> dict[str, Any]:
        """Append one effect to `trial`'s ledger; the sink answers 409 if another is open."""
        return self._call(
            "POST", "/effects", {"trial": trial.__dict__, "effect": effect.model_dump()}
        )

    def next_attempt(self, trial: TrialId, key: str) -> int:
        body = {"trial": trial.__dict__, "key": key}
        return int(self._call("POST", "/attempts/next", body)["attempt"])

    def _read(self, path: str, trial: TrialId) -> dict[str, Any]:
        query = urllib.parse.urlencode(trial.__dict__)
        answer = self._call("GET", f"{path}?{query}")
        if answer.get("trial") != trial.__dict__:
            raise SinkError(f"GET {path}: answered for trial {answer.get('trial')}, not {trial}")
        return answer

    def ledger(self, trial: TrialId) -> list[dict[str, Any]]:
        """Every row of `trial`'s ledger; the sink answers 409 if another trial is open."""
        return cast(list[dict[str, Any]], self._read("/ledger", trial)["rows"])

    def verify(self, trial: TrialId) -> dict[str, Any]:
        """The hash-chain check of `trial`'s ledger."""
        answer = self._read("/ledger/verify", trial)
        return {k: answer[k] for k in ("ok", "rows", "first_bad")}


def trial_of(history: HistoryWriter) -> TrialId:
    run = history.run
    return TrialId(batch=run.batch, cell=run.cell, trial=run.trial, seed=run.seed)


def record_effect(history: HistoryWriter, client: SinkClient, effect: EffectRequest) -> int:
    """POST one effect with its request and response events; returns the sink's sink_seq.

    The effect's worker and pid must be the history writer's: the merge links the sink's append
    to this worker's events by that name. The trial is the writer's too, and the sink refuses
    the effect if a different trial is open.

    If the sink refuses the request (a 4xx answer), `effect.response` has status fail: the effect
    was not applied. If it does not answer, the status is info: the effect may or may not be in
    the ledger, which only the ledger can say. Either way SinkError propagates.
    """
    if (effect.worker, effect.pid) != (history.proc, history.pid):
        raise ValueError(
            f"effect from {effect.worker} (pid {effect.pid}) recorded by "
            f"{history.proc} (pid {history.pid})"
        )
    fields: dict[str, Any] = {
        "op_id": effect.op_id,
        "logical_op": effect.logical_op,
        "effect_key": effect.effect_key,
        "attempt": effect.attempt,
        "thread_id": effect.thread_id,
        "checkpoint_id": effect.checkpoint_id_seen,
    }
    history.emit(
        "effect.request", **fields, data={"node": effect.node, "payload_hash": effect.payload_hash}
    )
    try:
        answer = client.post_effect(trial_of(history), effect)
    except SinkError as exc:
        status = "fail" if exc.refused else "info"
        history.emit("effect.response", **fields, status=status, error=str(exc))
        raise
    history.emit("effect.response", **fields, status="ok", data={"sink_seq": answer["sink_seq"]})
    return int(answer["sink_seq"])
