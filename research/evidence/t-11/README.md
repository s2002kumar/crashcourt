# T-11 evidence: the instrument against known defects

Controls and published replications only. Nothing here is a result about LangGraph: the saver mutants are
test-only defects, and the K1a and K3 shapes reproduce results already published (langgraph#8039 and
#8796). No new-evidence cell was run, and no schedule that would answer one. These are development runs from
unregistered scenarios; the pre-registered batches run only through the gate once the pre-registration is
final.

All runs: WSL2 (Linux), the isolated compose stack (Postgres 18.6, Toxiproxy 2.12.0, the effect sink built
from the commit run), LangGraph 1.2.12, langgraph-checkpoint-postgres 3.1.2, frozen checker v1 (`d8095168...`,
FROZEN.md `ddff87d4...`), from a clean checkout. Raw trials are local, not in git.

| Files | What | Commit |
| --- | --- | --- |
| `c5.json`, `c5.md`, `mechanisms.json` | C5 under the first matrix's revised methodology | e389ae9, on main add5c0e |
| `shapes.json` | C3, K1a, and K3 shapes, with #31's explicit landmarks | e389ae9 |
| `c5-original.json`, `c5-original.md`, `mechanisms-original.json` | the original C5 measurement, kept as it was | ae691a2 |
| `m5-w5-exploratory.json` | the exploratory M5 run on W5 that informed the revision, kept as it was | ae691a2 |
| `shapes-original.json` | the shapes before #31 named their landmarks | ae691a2 |

ae691a2 is this branch before its restack onto main add5c0e (PR #32's history keeps it); its mutants,
driver, and C5 shape are the ones the original report hashes.

## C5, the first matrix's methodology (`c5.json`, `c5.md`)

Revised before the pre-registration was final, after the original measurement below failed. The
pre-registration states it in the words of `crashcourt.experiment.controls.C5_REQUIREMENT`, and the gate
refuses a report that says anything else.

**Coverage: 3 of the 5 specified saver mutants are applicable to the first matrix and measured; M1 and M2 are
NOT EXERCISED and are not counted as passing.** Applicable mutants at 19 of 20: all three pass.

| Mutant | Defect | Shape | Intended invariant | Caught | Target's outcome in the 20 trials | Other invariants with an anomaly |
| --- | --- | --- | --- | --- | --- | --- |
| M3 | `put` stores no parent | W1 approve | I5a | 20 of 20 | GAP 20 | I8 20 |
| M4 | the second `put` of each op is written in a transaction whose commit fails; the error is swallowed and the put reports success | W1 approve | I1b | 20 of 20 | VIOLATION_CANDIDATE 20 | I5b 20, I8 20 |
| M5 | the latest-checkpoint read returns the head's parent | W5 sequential | I6b | 20 of 20 | GAP 20 | I5a 20 |

Not exercised by the first matrix, never run as part of its C5, and never PASS:

| Mutant | Defect | Declared target | Status | Why |
| --- | --- | --- | --- | --- |
| M1 | a `put_writes` call that writes two or more rows does not store its second row | I7a | NOT_EXERCISED | a dropped pending-write row is read back only by a run that takes an op over after a crash that follows the row's commit, and that schedule is K1b's new-evidence question, which no control may preview |
| M2 | `get_tuple` by checkpoint id returns None | I5a | NOT_EXERCISED | none of the first matrix's workloads reads a checkpoint by id |

This is deferred coverage, not a pass: the instrument's sensitivity to a lost pending-write row and to a by-id
read that finds nothing is not measured by the first matrix, and every results page that cites C5 states it.

Did each defect act? From the trials' own histories and snapshots, not the checker (`mechanisms.json`): M3 left
every checkpoint row without a parent in 20 of 20; M4 left 2 acknowledged checkpoints missing in each of its
20; M5 answered a latest read with an older id than one the worker had acknowledged in 20 of 20.

## C5, the original measurement (`c5-original.json`, `c5-original.md`)

Kept as it was. A failed instrument-design check before the pre-registration was final, not a finding about
LangGraph: it showed that M1 and M2 are not exercised by the first matrix's workloads and that W1 is the wrong
workload for M5. Each mutant ran 20 trials of the shape #30 fixed before any run: W1 approve, sync, one
worker, no fault.

| Mutant | Target | Caught | Target's outcome in the 20 trials | Other invariants with an anomaly |
| --- | --- | --- | --- | --- |
| M1 | I7a | 0 of 20 | NO_ANOMALY_UNSPECIFIED 20 | none |
| M2 | I5a | 0 of 20 | NO_ANOMALY_UNSPECIFIED 20 | none |
| M3 | I5a | 20 of 20 | GAP 20 | I8 20 |
| M4 | I1b | 20 of 20 | VIOLATION_CANDIDATE 20 | I5b 20, I8 20 |
| M5 | I6b | 0 of 20 | INVALID 20 (trigger 15) | none |

- **M1** dropped the second row of every multi-row `put_writes` call in 20 of 20 (`mechanisms-original.json`),
  but W1 never reads a pending write back in a run without a fault.
- **M2** never acted: no trial read a checkpoint by id, and no read in T-09's recorded W5 histories did
  either. LangGraph 1.2.12 reads by id only when the caller passes a `checkpoint_id`
  (`pregel/_loop.py`, `AsyncPregelLoop.__aenter__`), and no op in W1 or W5 does.
- **M5** acted in every trial: resume-1 read the head's parent, started before the interrupt, interrupted
  again on a new branch, and returned ok without its charge. v1's I3 harness alarm (trigger 15: a client ok
  whose model path has no ledger row) made every trial INVALID before I6b was judged. I3 is unchanged.

**Exploratory, not counted** (`m5-w5-exploratory.json`): M5 on W5 sequential, which posts no effects, so the
I3 alarm cannot fire: caught by I6b in 20 of 20 valid trials, with the fork on I5a in all 20. It is the run
that showed W5 to be M5's workload; the revised C5 above measured it again as part of the control.

## C3, K1a, K3 shapes (`shapes.json`)

Twenty trials of each shape (`tests/integration/scenarios/t11-*.yaml`, every armed step naming its
landmark), judged by the frozen checker and compared with the draft pre-registration's predictions for the
cell of the same shape, outcomes and observations alike. The same results as before #31
(`shapes-original.json`).

| Shape | Cell of the same shape | Trials | Valid | As predicted | Outcomes (each in all 20) |
| --- | --- | --- | --- | --- | --- |
| C3 on W1 (inert arms at L-GET, L-RES before and after, L-EXW before and after, L-EXC; a 0 ms toxic) | C3-w1-sync | 20 | 20 | 20 | I1a, I1b, I11 PASS; I4, I5a-c, I6a-b, I7a-c, I8 NO_ANOMALY_UNSPECIFIED |
| C3 on W5 (inert arm at L-T1; a 0 ms toxic; two turns) | C3-w5-sync | 20 | 20 | 20 | I1a, I1b, I11 PASS; I5a-c, I6a-b NO_ANOMALY_UNSPECIFIED |
| K1a (langgraph#8039) | K1a | 20 | 20 | 20 | I4 GAP, I7a GAP; I1a, I1b, I11 PASS; I2, I5a-c, I6a-b, I7b-c, I8 NO_ANOMALY_UNSPECIFIED; ledger: two charge rows, attempts 1 and 2 |
| K3 (langgraph#8796, held) | K3 | 20 | 20 | 20 | I5a GAP (fork), I6a GAP; I1a, I1b, I11 PASS; I5b-c NO_ANOMALY_UNSPECIFIED |

- C3: every trial valid, so every inert arm's `fp.hit` sat at its named landmark (the checker's triggers 10
  and 11), and the runner confirmed the placebo toxic installed and removed before the snapshot.
- K1a: SIGKILL at L-EXW (`put_writes.before`), `worker.exit` recorded, `dispatch.redeliver` of resume-1 to w2,
  one invoke and one terminal event (w2's `client.ok`); two charge rows with attempts 1 and 2.
- K3: w1 held at L-T1, turn-1 redelivered to w2, w2's `client.ok`, then w1 released; w1's stale result is
  noted in trial.json, never as a client event; the fork is on I5a.

## Reproduce

From a clean checkout on Linux with the compose stack up:

    uv run python -m tests.mutants.c5 --trials 20 --out <dir> --report c5.json
    uv run python research/evidence/t-11/mechanisms.py <C5 runs dir> mechanisms.json
    uv run python research/evidence/t-11/shapes.py --trials 20 --out <dir> --report shapes.json
