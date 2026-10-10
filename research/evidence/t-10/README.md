# T-10 evidence: the landmark audit on real fault-free histories

PRE-FREEZE. Validation of the checker for the freeze; nothing here is a result about LangGraph, and no fault
cell was run.

## Corpora

| Corpus | Recorded | Code | Report |
| --- | --- | --- | --- |
| Final | 2026-10-10, batches `t09-*-20261010T093643Z` at 2224b17 (tree-identical to T-09 as merged, 6aea04e): the batches whose evidence is in research/evidence/t-09/, same batch ids and manifest hashes | T-07 + T-09 as merged | `validation-t09-final.json` |
| Development | 2026-10-10, T-09 at 194c94a, before the restack | T-07 + T-09 pre-merge | `validation-t09-development.json` |

Each: five canonical batches of 50 fault-free trials (W1 approve, W1 reject, W1 misuse, W5 sequential, W5
concurrent, the last being control C2's forced fork), every trial valid by the orchestrator. Ledgers copied
read-only from the sink container; the live saver rows (compose project cct07, direct port 55452) dumped with
`crashcourt.snapshot.dump.take`. Local data, not in git.

Run: `uv run python -m tests.controls.t09 <corpus> <ledger-copy> <report-dir>`.

## Reconstructed, because the runner does not yet write them

The checker reads `ledger.json`, `snapshot.json` (two snapshots, pre-trial emptiness counts), and
`sink-health.json` beside T-07's files; without them every real trial is INVALID (trigger 1). The adapter
built them: ledger rows from the sink's SQLite file, chain verified by T-08's `Ledger.verify` (ok in all 500
trials); first snapshot from T-09's `db-rows.jsonl` export, second from a live dump (the same rows in every
trial); pre-trial emptiness not recorded, set to zero (thread ids are trial-scoped); the sink reading from the
batch's run.json; the landmark table applied as if audited.

## Results (both corpora identical)

| Batch | Trials | INVALID | False anomalies | Outcomes (each 50 of 50) |
| --- | --- | --- | --- | --- |
| W1 approve | 50 | 0 | 0 | I1a, I1b, I11 PASS (LG-DUR-SYNC); I4 PASS (LG-PW-EXC); I5a-c, I6a-b, I7a-c, I8 NO_ANOMALY_UNSPECIFIED |
| W1 reject | 50 | 0 | 0 | I1a, I1b, I11 PASS; I5, I6 NO_ANOMALY_UNSPECIFIED; I2, I4, I7, I8 not exercised (no effect) |
| W1 misuse | 50 | 0 | 0 | as approve, with I4 EXPECTED (LG-INT-RERUN): the precheck the model places before interrupt() |
| W5 sequential | 50 | 0 | 0 | I1a, I1b, I11 PASS; I5, I6 NO_ANOMALY_UNSPECIFIED |
| W5 concurrent (C2) | 50 | 0 | 0 | I5a GAP (fork) and I6a GAP (head by the larger id); durability NO_ANOMALY_UNSPECIFIED (two writers, disclaimed) |

The fork in W5 concurrent is the control's forced shape, not a discovery; that the head was the same branch in
all 50 follows from w1 being released first and is an observation, not a guarantee.

Artifact mutants on a real W1 approve trial: AM1, AM2, AM3, AM4, AM5, AM6, AM8, AM10 each gave the outcome the
specification names; on a real W5 concurrent trial, AM2 and AM4. AM7 and AM9 need a fault trial and are covered
by the synthetic tests. Determinism: 15 sampled real trials gave byte-identical output across reruns, shuffled
histories, and shuffled snapshot rows. On the final corpus, every one of the 1,000 `node.start` events names the
same checkpoint as its task's `put_writes`, and all 7,500 saver events carry an op id.

## Landmarks

Derived from the final corpus and identical in the development corpus: each landmark found once per run, at the
same occurrence in all 50 trials of each batch. The occurrence counts calls of the method in that worker and op,
as T-06's registry counts them; it is the number a fault scenario's arm names.

| Landmark | Rule (scenarios/landmarks/langgraph-v1.yaml) | Where | Occurrence | 4.2's hypothesis |
| --- | --- | --- | --- | --- |
| L-IN | put; source input; no node.start before it in the op | W1 start-1; every W5 turn | 1 | 1 |
| L-RES | put_writes with `__resume__`, of the approval node's task | W1 resume-1 | 2 | 1 |
| L-EXW | put_writes of execute's task, after execute's node.end | W1 resume-1 | 3 | (execute's batch) |
| L-EXC | put after execute's node.end, before record's node.start | W1 resume-1 | 2 | 2 |
| L-T1 | put after the model node (`respond`) ends | every W5 turn | 3 | 2 |
| L-GET | the op's first get_tuple | every op | 1 | (not in 4.2) |

- L-RES: my ruling (2026-10-10) names the RESUME write for the interrupted task. A resume run first
  writes the null task's copy of the Command's value (`pregel/_io.py:75`); that is occurrence 1 and is not the
  landmark, so 4.2's guessed occurrence would have armed the wrong call.
- L-T1: a W5 turn writes its input checkpoint, the checkpoint after the input step, then the one after
  `respond`: the third put, not the second.
- L-GET: control C2 holds both workers at their turn's first read; every hit outside a synthetic trial must sit at
  a landmark.

## Fixed while auditing

- Snapshots were compared as ordered tuples; a dump returns rows in the database collation's order, so identical
  snapshots read as reconciled. Now compared and evaluated in key order.
- The loader read T-09's `db-rows.jsonl` as a history; it now reads the histories trial.json lists.
- L-T1 named the model node `model`; T-09 names it `respond`.
