# T-09 evidence: fault-free W1 and W5

Recorded 2026-10-10 at commit 2224b17, the head squash-merged as 6aea04e (identical trees; on main ba6a721 the
src, tests, scenarios, and uv.lock trees are the same), from a clean checkout on Linux (WSL2, kernel
6.6.87.2-microsoft-standard-WSL2) with Docker Desktop, the pinned images, and the sink built from the same checkout;
sink clock offsets 0.10 to 0.22 ms. Before the batches, on that checkout: ruff, ruff format, and pyright strict clean,
680 unit and 36 integration tests passed. This replaces an earlier recording at 194c94a, made before the branch was
restacked onto main, with the same results.
Every batch ran in canonical mode with 50 trials, no faults, and no arms other than W5 concurrent's two barriers.
Each directory holds the batch's manifest, batch record, run.json, summary, and scenario, and the first trial's
trial.json, merged history (merged.jsonl, which includes the sink's ledger rows), and the same history rendered one
line per event (merged.txt, by research/evidence/t-07/render.py). The other trials' raw histories are not kept in git.

| Batch | Scenario | Valid | Ledger rows per trial | Saved head state | Lineage per trial |
| --- | --- | --- | --- | --- | --- |
| t09-w1-approve | scenarios/w1-approve.yaml | 50 of 50 | one `charge`, attempt 1, from execute in resume-1 | decision approve, executed [charge, 1] | 6 checkpoints, one root, no fork |
| t09-w1-reject | scenarios/w1-reject.yaml | 50 of 50 | none | decision reject, executed [] | 6 checkpoints, one root, no fork |
| t09-w1-misuse | scenarios/w1-misuse.yaml | 50 of 50 | `precheck` attempt 1 (start-1) and attempt 2 (resume-1), one `charge` | decision approve, executed [charge, 1] | 6 checkpoints, one root, no fork |
| t09-w5-sequential | scenarios/w5-sequential.yaml | 50 of 50 | none | both turns and both answers | 6 checkpoints, one chain |
| t09-w5-concurrent | scenarios/w5-concurrent.yaml | 50 of 50 | none | turn 1, then one of the two new turns | 9 checkpoints, one root, one parent with two children |

determinism.txt is the report of determinism.py over all five batches: within each batch, all 50 trials share one
signature (ledger rows, terminal client events and outputs, and each thread's saved head state and lineage shape,
read from Postgres with strict msgpack, with the trial's thread prefix, sequence numbers, times, and checkpoint ids
removed).

W5 concurrent is a forced fork by construction: both new turns read the same head and are released together. That
every trial forked is what the scenario is for. That the head was the same branch in all 50 (w2's turn, whose answer
is attempt 2 of turn 1) is an observation, not a property: w1's release goes out first, so on this machine w1
answered and committed first and w2's checkpoint, written last, has the larger id. Classifying the fork and the head
is the checker's job (T-10).

What to read in each merged.txt:

- **w1-approve**: plan, then approval interrupted (`node.end` outcome interrupted) under start-1; under resume-1 the
  null-task resume write, approval again (attempt 2, the same task id), execute, whose `effect.request` carries
  `decision=approve` and names the checkpoint execute ran from, then `sink.append` and the ok `effect.response`
  with the same ledger row, then record.
- **w1-misuse**: the same, plus a `precheck` request in each approval attempt: attempt 1 before the pause and
  attempt 2 on resume.
- **w1-reject**: no effect events at all; record saves decision reject.
- **w5-sequential**: two runs of respond on one thread, turn 0 and turn 1, each attempt 1; each run's checkpoints
  name the previous run's last checkpoint as parent.
- **w5-concurrent**: turn-1 on w1, then turn-2 (w1) and turn-3 (w2) both parked at `get_tuple.after` having read the
  same checkpoint, both released together, and both answered as turn 1 with attempts 1 and 2.

Commands (run from the checkout, with the compose services up and the sink built from it):

```bash
uv run crashcourt run scenarios/w1-approve.yaml --trials 50 --canonical
uv run crashcourt run scenarios/w1-reject.yaml --trials 50 --canonical
uv run crashcourt run scenarios/w1-misuse.yaml --trials 50 --canonical
uv run crashcourt run scenarios/w5-sequential.yaml --trials 50 --canonical
uv run crashcourt run scenarios/w5-concurrent.yaml --trials 50 --canonical
uv run python research/evidence/t-09/determinism.py runs/<batch> ...
```
