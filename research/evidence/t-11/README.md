# T-11 evidence: the instrument against known defects

Controls and published replications only. Nothing here is a result about LangGraph: the saver mutants are
test-only defects, and the K1a and K3 shapes reproduce results already published (langgraph#8039 and
#8796). No new-evidence cell was run, and no schedule that would answer one. These are development runs from
an unregistered scenario; the pre-registered batches run only through the gate once the pre-registration is
final.

All runs: WSL2 (Linux), the isolated compose stack (Postgres 18.6, Toxiproxy 2.12.0, the effect sink built
from that commit), LangGraph 1.2.12, langgraph-checkpoint-postgres 3.1.2, a clean checkout of ae691a2 (this
branch), frozen checker v1 (`d8095168...`, FROZEN.md `ddff87d4...`). Raw trials are local, not in git.

## C5: saver mutants (`c5.json`, `c5.md`)

Each mutant ran 20 trials of C5's shape as fixed in #30 before any run: W1 approve, sync, one worker, no
fault. A trial catches its mutant when the checker made it valid and found an anomaly on the target.

| Mutant | Defect | Target | Caught | Target's outcome in the 20 trials | Other invariants with an anomaly |
| --- | --- | --- | --- | --- | --- |
| M1 | a `put_writes` call that writes two or more rows does not store its second row | I7a | 0 of 20 | NO_ANOMALY_UNSPECIFIED 20 | none |
| M2 | `get_tuple` by checkpoint id returns None | I5a | 0 of 20 | NO_ANOMALY_UNSPECIFIED 20 | none |
| M3 | `put` stores no parent | I5a | 20 of 20 | GAP 20 | I8 20 |
| M4 | the second `put` of each op is written in a transaction whose commit fails; the error is swallowed and the put reports success | I1b | 20 of 20 | VIOLATION_CANDIDATE 20 | I5b 20, I8 20 |
| M5 | the latest-checkpoint read returns the head's parent | I6b | 0 of 20 | INVALID 20 (trigger 15) | none |

Did each defect act? Read from the trials' own histories and snapshots, not from the checker
(`mechanisms.json`): M1 left one row fewer for every multi-row call in 20 of 20 trials; no trial of any
mutant read a checkpoint by id; M3 left every checkpoint row without a parent in 20 of 20; M4 left 2
acknowledged checkpoints missing in each of its 20 trials; M5 answered a latest read with an older id than
one the worker had acknowledged in 20 of 20.

**Result: FAIL**, three mutants below 19 of 20. Each failure has a cause in the workloads or in the frozen
checker, not in the harness:

- **M1** drops the second row of every multi-row `put_writes` call, in every trial (`mechanisms.json`). W1
  never reads a pending write back in a run without a fault, so nothing downstream can differ. A dropped row
  is read back only when a crash follows its commit and another run takes the op over, which is the question
  K1b, N11, and A2 ask.
- **M2** never acts: no trial read a checkpoint by id (`mechanisms.json`), and no read in T-09's recorded W5
  histories did either. LangGraph 1.2.12 reads by id only when the caller passes a `checkpoint_id`
  (`pregel/_loop.py`, `AsyncPregelLoop.__aenter__`), and no op in W1 or W5 does.
- **M5** acts in every trial: resume-1 reads the head's parent. The resume then starts before the interrupt,
  approval interrupts again on a new branch (a fork), and resume-1 returns ok without its charge. v1's I3
  harness alarm (trigger 15: a client ok whose model path has no ledger row) makes every trial INVALID before
  I6b is judged.

M3 and M4 were caught in every trial: M3 by I5a (every checkpoint a root; I8's anomaly in all 20 reads "no
RESUME write in the head lineage", since the head's lineage is the head alone), M4 by I1b as
VIOLATION_CANDIDATE (two acknowledged checkpoints missing per trial; I5b's anomalies name the dangling
parents they leave).

### Exploratory, not C5 (`m5-w5-exploratory.json`)

M5 in W5's sequential C1 shape, which posts no effects, so the I3 alarm cannot fire: caught by I6b in 20
of 20 valid trials (GAP), with the fork on I5a in all 20. This does not count toward C5 and does not change
C5's shape; whether M5 should run there is my decision.

## C3, K1a, K3 shapes (`shapes.json`)

Twenty trials of each shape (`tests/integration/scenarios/t11-*.yaml`), judged by the frozen checker and
compared with the draft pre-registration's predictions for the cell of the same shape, outcomes and
observations alike.

| Shape | Cell of the same shape | Trials | Valid | As predicted | Outcomes (each in all 20) |
| --- | --- | --- | --- | --- | --- |
| C3 on W1 (six inert arms: L-GET, L-RES before and after, L-EXW before and after, L-EXC; a 0 ms toxic) | C3-w1-sync | 20 | 20 | 20 | I1a, I1b, I11 PASS; I4, I5a-c, I6a-b, I7a-c, I8 NO_ANOMALY_UNSPECIFIED |
| C3 on W5 (inert arm at L-T1; a 0 ms toxic; two turns) | C3-w5-sync | 20 | 20 | 20 | I1a, I1b, I11 PASS; I5a-c, I6a-b NO_ANOMALY_UNSPECIFIED |
| K1a (langgraph#8039) | K1a | 20 | 20 | 20 | I4 GAP, I7a GAP; I1a, I1b, I11 PASS; I2, I5a-c, I6a-b, I7b-c, I8 NO_ANOMALY_UNSPECIFIED; ledger: two charge rows, attempts 1 and 2 |
| K3 (langgraph#8796, held) | K3 | 20 | 20 | 20 | I5a GAP (fork), I6a GAP; I1a, I1b, I11 PASS; I5b-c NO_ANOMALY_UNSPECIFIED |

- C3: all 40 trials valid, so every inert arm's `fp.hit` sat at its audited landmark (the checker's
  triggers 10 and 11), and every trial.json shows the placebo toxic installed, confirmed by Toxiproxy, and
  removed before the snapshot (40 of 40).
- K1a: SIGKILL at L-EXW (`put_writes.before`), `worker.exit` recorded, `dispatch.redeliver` of resume-1 to w2,
  one invoke and one terminal event (w2's `client.ok`); two charge rows with attempts 1 and 2.
- K3: w1 held at L-T1, turn-1 redelivered to w2, w2's `client.ok`, then w1 released; w1's stale result
  ("w1 answered ok") is noted in trial.json in 20 of 20, never as a client event; the fork is on I5a.

## Reproduce

From a clean checkout on Linux with the compose stack up:

    uv run python -m tests.mutants.c5 --trials 20 --out <dir> --report c5.json
    uv run python research/evidence/t-11/shapes.py --trials 20 --out <dir> --report shapes.json
    uv run python research/evidence/t-11/m5_w5_exploratory.py --trials 20 --out <dir> --report m5-w5-exploratory.json
    uv run python research/evidence/t-11/mechanisms.py <C5 runs dir> mechanisms.json
