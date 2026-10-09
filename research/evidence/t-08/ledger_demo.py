"""Write a demonstration ledger dump from the compose sink (T-08 evidence). Not a result.

Opens trial DEMO-t08/DEMO-ledger/2 on the sink at 127.0.0.1:8480, records a charge twice (the
same effect key, attempts 1 and 2) and one refund, takes two attempt numbers, then writes every
ledger row and the chain verification to ledger-dump.json next to this file.

    docker compose up -d --wait && uv run python research/evidence/t-08/ledger_demo.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from crashcourt.sink.client import SinkClient
from crashcourt.sink.ledger import EffectRequest, TrialId

HERE = Path(__file__).resolve().parent
TRIAL = TrialId("DEMO-t08", "DEMO-ledger", 2, "0")


def effect(key: str, attempt: int, payload: bytes) -> EffectRequest:
    return EffectRequest(
        effect_key=key,
        logical_op="approve:t3:i1",
        op_id=f"op-{attempt}",
        attempt=attempt,
        worker="worker-1",
        pid=os.getpid(),
        thread_id="t3",
        node="execute",
        checkpoint_id_seen="C4",
        payload_hash=hashlib.sha256(payload).hexdigest(),
    )


def main() -> None:
    client = SinkClient()
    opened = client.open_trial(TRIAL)
    if opened["rows"]:
        raise SystemExit("the demo trial already has rows; remove the sink-data volume first")
    attempts = [client.next_attempt(TRIAL, "model/t3/execute/0") for _ in range(2)]
    seqs = [
        client.post_effect(TRIAL, effect("charge:order-17", 1, b"charge 17"))["sink_seq"],
        client.post_effect(TRIAL, effect("charge:order-17", 2, b"charge 17"))["sink_seq"],
        client.post_effect(TRIAL, effect("refund:order-17", 2, b"refund 17"))["sink_seq"],
    ]
    dump = {
        "note": "Demonstration data (DEMO-), not a result.",
        "attempts_returned": attempts,
        "sink_seqs_returned": seqs,
        "rows": client.ledger(TRIAL),
        "verify": client.verify(TRIAL),
    }
    (HERE / "ledger-dump.json").write_bytes((json.dumps(dump, indent=2) + "\n").encode())
    print(json.dumps(dump["verify"]))


if __name__ == "__main__":
    main()
