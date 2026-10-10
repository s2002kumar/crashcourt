# T-10 evidence: landmark derivation and validation on real fault-free histories

PRE-FREEZE. The derivation below is for the landmark audit; the checker is not frozen and nothing here is a
result about LangGraph.

## Inputs

- T-09's five canonical batches of 50 fault-free trials each, recorded 2026-10-10 at commit 194c94a (T-09's
  branch, stacked on T-07), every trial valid by the orchestrator: W1 approve, W1 reject, W1 misuse, W5
  sequential, and W5 concurrent (control C2's forced fork). Local data, not in git.
- Their ledgers, copied read-only from the sink container (`/data/<batch>/<cell>/trial-NNNNN.sqlite`).
- The live saver rows of the same threads (compose project cct07, direct port 55452), dumped with
  `crashcourt.snapshot.dump.take`.
- Checker at this branch, clause registry `05cbc2b4...0ec7`.

Run: `uv run python -m tests.controls.t09 <t09-data> <ledger-copy> <report-dir>`. The machine-readable result
is `validation-t09-fault-free.json` here.

## Reconstructed, because the runner does not yet write them

The checker reads `ledger.json`, `snapshot.json` (two snapshots and the pre-trial emptiness counts), and
`sink-health.json` beside T-07's files. T-09's trials do not have them, so the adapter built them, and as
recorded these trials would be INVALID (trigger 1) until a post-trial step writes them:

- ledger rows from the sink's SQLite file, the chain verified by T-08's `Ledger.verify` (every trial ok);
- the first snapshot from T-09's `db-rows.jsonl` export, the second from a live dump now; the two hold the
  same rows in every trial;
- pre-trial emptiness: not recorded; set to zero (thread ids are scoped to the trial);
- the sink reading after the trial: the batch's `/health` reading from run.json;
- the draft landmark table applied as if audited.

## Results (250 trials)

| Batch | Trials | INVALID | False anomalies | Outcomes |
| --- | --- | --- | --- | --- |
| W1 approve | 50 | 0 | 0 | I1a, I1b, I11 PASS (LG-DUR-SYNC); I4 PASS (LG-PW); I5-I8 NO_ANOMALY_UNSPECIFIED |
| W1 reject | 50 | 0 | 0 | I1a, I1b, I11 PASS; I5, I6 NO_ANOMALY_UNSPECIFIED; I4, I7, I8 not exercised (no effect) |
| W1 misuse | 50 | 0 | 0 | as approve, but I4 EXPECTED (LG-INT-RERUN): the precheck the model places before interrupt() |
| W5 sequential | 50 | 0 | 0 | I1a, I1b, I11 PASS; I5, I6 NO_ANOMALY_UNSPECIFIED |
| W5 concurrent (C2) | 50 | 0 | 0 | I5a GAP (fork) and I6a GAP (head by the larger id) in 50 of 50; durability NO_ANOMALY_UNSPECIFIED (two writers) |

Artifact mutants on a real W1 approve trial: AM1, AM2, AM3, AM4, AM5, AM6, AM8, AM10 each gave the outcome
the specification names; on a real W5 concurrent trial, AM2 and AM4. (AM7 and AM9 need a fault trial; they
are covered by the synthetic tests.)

## Derived landmarks

Each landmark located in every trial of its batch, at the same occurrence in all 50 (the occurrence counts
calls of the method in that worker and op, as T-06's registry counts them):

| Landmark | Rule (draft table) | Where | Occurrence | 4.2's hypothesis |
| --- | --- | --- | --- | --- |
| L-IN | put, source input, no node.start before | W1 start-1; each W5 turn | 1 | 1 |
| L-EXW | put_writes of execute's task, after its node.end | W1 resume-1 | 3 | (the batch for execute) |
| L-EXC | put after execute's node.end, before record's node.start | W1 resume-1 | 2 | 2 |
| L-T1 | put after W5's model node (`respond`) ends | each W5 turn | 3 | 2 |
| L-GET (proposed) | the op's first get_tuple | every op | 1 | not in 4.2 |
| L-RES | put_writes carrying `__resume__` | W1 resume-1 | ambiguous | 1 |

For the audit:

1. **L-RES matches two calls in every resume run**: the null task's write of the Command's resume value and
   the interrupted node's own RESUME list (also visible in the saver rows). 4.2's "occurrence 1" is the null
   task's; the generic rule `first_call` would select it.
2. **L-T1 is the third put of a turn, not the second**: a W5 turn writes its input checkpoint, then the
   checkpoint after the input step, then the one after `respond`. 4.2's guessed occurrence would have armed
   the wrong call. The rule's node name is corrected to `respond`, T-09's name for the model node.
3. **L-GET is proposed for control C2**, whose barriers hold both workers at their turn's first read; every
   hit outside a synthetic trial must sit at a landmark.

## Fixed in the checker while validating

Snapshots were compared as ordered tuples, and a dump returns rows in the database collation's order
(`en_US` orders `__start__` and `branch:to:...` differently from Python), so identical snapshots read as
different and every trial was noted as reconciled. Snapshots are now compared and evaluated in key order, the
dump writes them in key order, and a test shuffles rows and requires the same bytes. The loader also read
T-09's `db-rows.jsonl` as a process history; it now reads the histories trial.json lists.
