"""A worker's client for the effect sink, and the effect events it records around each call.

`record_effect` writes `effect.request` before the POST and `effect.response` after it, carrying
the sink's `sink_seq`, so the merger can order request, `sink.append`, and response (T-04).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, cast

from crashcourt.history.writer import HistoryWriter
from crashcourt.sink.ledger import EffectRequest, TrialId

DEFAULT_URL = "http://127.0.0.1:8480"


class SinkError(Exception):
    """The sink did not confirm the request; the effect's outcome is unknown to the caller."""


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
            raise SinkError(f"{method} {path}: HTTP {exc.code} {detail}") from exc
        except OSError as exc:  # refused, reset, timed out: the outcome is unknown
            raise SinkError(f"{method} {path}: {type(exc).__name__}: {exc}") from exc

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health")

    def open_trial(self, trial: TrialId) -> dict[str, Any]:
        return self._call("POST", "/trials", trial.__dict__)

    def post_effect(self, effect: EffectRequest) -> dict[str, Any]:
        return self._call("POST", "/effects", effect.model_dump())

    def next_attempt(self, key: str) -> int:
        return int(self._call("POST", "/attempts/next", {"key": key})["attempt"])

    def ledger(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self._call("GET", "/ledger")["rows"])

    def verify(self) -> dict[str, Any]:
        return self._call("GET", "/ledger/verify")


def record_effect(history: HistoryWriter, client: SinkClient, effect: EffectRequest) -> int:
    """POST one effect with its request and response events; returns the sink's sink_seq.

    If the sink does not answer, `effect.response` records the failure and SinkError propagates:
    the effect may or may not be in the ledger, which only the ledger can say.
    """
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
        answer = client.post_effect(effect)
    except SinkError as exc:
        history.emit("effect.response", **fields, status="info", error=str(exc))
        raise
    history.emit("effect.response", **fields, status="ok", data={"sink_seq": answer["sink_seq"]})
    return int(answer["sink_seq"])
