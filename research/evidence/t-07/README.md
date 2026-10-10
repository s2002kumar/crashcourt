# T-07 evidence

Recorded 2026-10-10 at commit ff54ef4 (T-07 rebased onto main 0f282b0), from a clean checkout on Linux: WSL2
(kernel 6.6.87.2-microsoft-standard-WSL2) with Docker Desktop, the pinned images, and the sink built from the same
checkout. WSL2 and Docker Desktop share one kernel, so the sink's clock offset was 0.10 to 0.23 ms in every batch.
Every batch ran in canonical mode (`--canonical`): a clean checkout and a sink clock within one second, or the
batch would have been refused. Each directory holds the batch's manifest, batch record, run.json, summary, and
scenario; the full ones also hold the first trial's trial.json, merged history (merged.jsonl), and the same history
rendered one line per event (merged.txt, by render.py). The other trials' raw histories are not kept in git.

| Batch | Scenario | Trials | Valid | Client outcomes |
| --- | --- | --- | --- | --- |
| t07-smoke | scenarios/smoke.yaml | 50 | 50 | 200 ok |
| t07-sequential-100 | tests/integration/scenarios/sequential-100.yaml | 1 | 1 | 100 ok |
| t07-kill-parked | tests/integration/scenarios/kill-parked.yaml | 1 | 1 | 1 info, 1 ok |
| t07-self-kill | tests/integration/scenarios/self-kill.yaml | 1 | 1 | 1 info, 1 ok |
| t07-timeout | tests/integration/scenarios/timeout.yaml | 1 | 1 | 1 info, 1 ok |

What to read in each:

- **t07-smoke/merged.txt**: both workers park at `get_tuple.after` (two `fp.hit`), both releases are logged before
  either acts (two `fp.release`, then two `fp.action`), and each `client.ok` names the worker's last event before
  it answered. op-3 and op-4 continue the thread the other worker wrote: their outputs hold four trace entries.
- **t07-kill-parked/merged.txt**: w1 parks at `put.before`, the step kills it (`fault.inject`, then `fault.heal`
  with returncode -9), `worker.exit` carries SIGKILL, and op-1 ends as `client.info` naming SIGKILL; op-2 on w2 is
  unaffected. trial.json records that w1 ended with `put` and `put_writes` in flight: under sync durability
  LangGraph writes a step's pending writes beside its checkpoint. Recorded, not judged.
- **t07-self-kill/merged.txt**: w1 sends itself SIGKILL at `put.before` (`fp.hit`, then `fp.action`, then nothing);
  the trial is valid because the step explains the status.
- **t07-timeout/merged.txt**: w1 parks for good; after `run_s` (3 s) the client records `client.info` with reason
  "no result within 3 s", op-2 runs, and w1 exits 0 on shutdown with its run abandoned.

The gates, checked live on the same commit and clean checkout:

```text
== dirty checkout, canonical
run refused: canonical batch refused: the checkout has uncommitted changes
exit 2
== sink clock 5 s ahead (a pass-through proxy shifting /health's clock)
-- canonical
run refused: canonical batch refused: manifest: the sink's clock is +5.001 s from this host's, beyond the 1 s limit; batch start: the sink's clock is +5.000 s from this host's, beyond the 1 s limit
exit 2
-- ordinary
warning: clock: manifest: the sink's clock is +5.000 s from this host's, beyond the 1 s limit; batch start: the sink's clock is +5.000 s from this host's, beyond the 1 s limit (recorded; never used to order events)
trial    1: ok       ok=4
trial    2: ok       ok=4
exit 0
```

clock-gate-ordinary-run.json is that ordinary batch's run.json, with the offsets and the warning as recorded.

Commands (run from the checkout, with the compose services up and the sink built from it):

```bash
uv run crashcourt run scenarios/smoke.yaml --trials 50 --canonical
uv run crashcourt run tests/integration/scenarios/sequential-100.yaml --canonical
uv run crashcourt run tests/integration/scenarios/kill-parked.yaml --canonical
uv run crashcourt run tests/integration/scenarios/self-kill.yaml --canonical
uv run crashcourt run tests/integration/scenarios/timeout.yaml --canonical
uv run python research/evidence/t-07/render.py runs/<batch>/<cell>/trial-00001/merged.jsonl
```
