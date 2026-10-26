# N8: Stale-worker recovery, checkpoint ownership, and external effects

**Status:** Four mitigation variants and the canonical three-round overhead benchmark have
completed. N8-F and N8-FI retain the frozen checker's I6a prediction mismatch; the
mitigation contract matched its predictions in all four variants. This report preserves the
original stopped study and post-outcome continuation separately. No documented LangGraph
guarantee violation is claimed.

## Abstract

A durable workflow has at least two observable histories: the state recorded by its checkpoint
store and the side effects already performed against the outside world. CrashCourt exercises a
precisely controlled schedule in which a worker applies a charge, is held before committing its
pending checkpoint writes, a replacement worker completes the same logical work, and the older
worker is released. An independent effect ledger, direct Postgres snapshots, and per-process
histories reveal whether the acknowledged run remains the checkpoint head and whether the charge
was applied twice.

In the **unprotected N8 baseline**, all 20 trials had a checkpoint fork and two applied charges.
With the **epoch-ownership fence N8-F**, all 20 trials rejected the stale worker's late writes,
preserved the acknowledged run's checkpoint as the selected head, and still applied **two charges**.
With idempotency alone (N8-I), 20/20 trials applied just one charge but retained the checkpoint
fork and displaced acknowledged head. With both protections (N8-FI), all 20 trials had a single
applied charge, no fork, and the acknowledged head—but all 20 saved states named the *duplicate*
attempt rather than the earlier attempt that actually applied the charge. The protections
address different properties; together they do not yet establish accurate attempt provenance.
These results are specific to a controlled fault schedule, not production failure frequencies.

## Why this matters

A successfully acknowledged recovery is insufficient evidence that the state most recently read
from the saver reflects that successful execution. Likewise, a single chosen checkpoint head
does not imply exactly-once external effects. CrashCourt evaluates these independently.

- **State witness:** the durable Postgres saver rows and the head selected by the real saver.
- **External witness:** a separately committed, hash-chained SQLite ledger; every attempted
  charge is recorded and classified as applied or duplicate where idempotency is enabled.
- **Execution witness:** client acknowledgments, worker lifetime, barrier crossings, and saver
  operations from independently written process histories.
- **Verdict:** the frozen v1 checker, plus an independently versioned mitigation contract.
  Neither a summary report nor a visualization recomputes or alters the oracle's outcomes.

## Pre-registered schedule

1. Worker A reads the thread and executes its charge.
2. A is held at task-write and checkpoint-save boundaries so its pending state is not committed.
3. The orchestrator redelivers the same logical operation to worker B while A is still alive.
4. B completes its execution and the client receives an acknowledgment.
5. Only then are A's held operations released. The harness reads the checkpoint tables and
   retrieves both workers' effects and histories.

The complete experimental protocol, controls, locked scenario hashes, predictions, stop conditions,
and post-run deviations are recorded in
[research/preregistration.md](preregistration.md). The study uses LangGraph 1.2.12,
langgraph-checkpoint-postgres 3.1.2, Postgres 18.6, and `durability="sync"`.

N8 is CrashCourt's composition of two previously known recovery mechanisms. K1a replicates a
reported duplicate external effect; K3 replicates a reported concurrent-writer checkpoint fork.
The component failures are **not** claimed as novel discoveries.

## Verified four-way mitigation comparison

The observations below are from the independently preserved baseline/fence study
[40155579729](https://github.com/s2002kumar/crashcourt/actions/runs/40155579729) and the
single continuation [40276230146](https://github.com/s2002kumar/crashcourt/actions/runs/40276230146),
both using the pinned experimental commit **4f4176bc185a0559db3b358ecbcbb7ae364c88d1**.
Each variant exercised 20 trials. The two jobs' full recorded environment fingerprints are
identical (all 24 batches share `4f9a9e6155b64ce0...`; the comparison is in the
[operations log](n8-mitigation-operations.md)), but they remain two jobs, the second started
after the first's outcome was known. The table sets their outcomes side by side and pools no
trials across runs.

| Observation | N8-base | N8-F | N8-I | N8-FI |
| --- | ---: | ---: | ---: | ---: |
| Checkpoint lineage fork | 20/20 | 0/20 | 20/20 | 0/20 |
| Acknowledged run is stored head | 0/20 | 20/20 | 0/20 | 20/20 |
| Logical operation has exactly one applied effect | 0/20 | 0/20 | 20/20 | 20/20 |
| Applied-effect attempt agrees with saved attempt reference | Not claimed | Not claimed | 20/20 | 0/20 |
| Stale writes rejected by an ownership fence | No fence | 20/20 | No fence | 20/20 |
| INVALID trials | 0 | 0 | 0 | 0 |
| Mitigation contract | As predicted | As predicted | As predicted | As predicted |

In the two unfenced cells the original and replacement worker can each attach a checkpoint
branch to the same parent. In the two cells without effect idempotency, both charge attempts
are **applied**, regardless of whether a fence protects checkpoint writes. Under N8-I and N8-FI,
the ledger records two requests, but only one is **applied**; the other is a duplicate.

The combined case is the key remaining limit: the durable checkpoint records the replacement
worker's charge attempt, whereas the sink applied the earlier request. Correct durable ownership
and exactly-once *logical effect application* are therefore insufficient to establish correct
attempt identity. This does not claim that LangGraph guarantees concurrent same-thread recovery:
that behavior remains outside the pinned runtime's documented contract.

All ten instrument cells and C9 completed as predicted in both study jobs. Both fence controls
agreed with their respective contracts. Original N8
[39061196254](https://github.com/s2002kumar/crashcourt/actions/runs/39061196254) and
the K1a/K3 known-behavior replications remain separate historical experiments.

### Frozen-checker mismatch, preserved without changing predictions

The independent mitigation contract was **as-predicted in 20/20 trials** of each of the four
variants. The frozen v1 checker is intentionally not identical to that contract. I6a flags
the head of a thread with overlapping live dispatches, even when fencing prevents any stale
checkpoint from becoming head. LangGraph's disclaimer makes its observation a GAP.

- **N8-F:** I6a was GAP in 20/20, but the frozen prediction allowed PASS or
  NO_ANOMALY_UNSPECIFIED. First study run 40155579729 stopped after this difference.
- **N8-I:** I6a GAP in 20/20 was within its already registered prediction. The
  continuation ran it, and both frozen checker and contract were as-predicted.
- **N8-FI:** I6a was GAP in 20/20, again outside its frozen predicted outcomes, so the
  continuation's overall GitHub job reports failure and stops. **The job's failure is a
  pre-registered prediction mismatch, not an INVALID cell or a discovered violation.**
  Its complete 20-trial artifacts, independent contract results, and head/effect evidence
  were still uploaded.

This disagreement was not corrected after observing N8-F. The separate continuation decision
was made after the original stop and is expressly a **protocol deviation**, with identical
registered predictions and a repeat of the ten instrument controls. The record lives in the
[preregistration deviations](preregistration.md#deviations) and the
[operations log](n8-mitigation-operations.md). No variants were silently rerun to produce a
more favorable study conclusion.

### Formal evidence location

The continuation artifact is
[mitigation-study-gh40276230146-1](https://github.com/s2002kumar/crashcourt/releases/download/evidence-2026-10/mitigation-study-gh40276230146-1.zip) (GitHub artifact 12630983255),
GitHub-reported SHA-256 **56d4d83c376c390404e8543b05ca84a2ea7ce5276273c05853b75d974a108deb**.
The baseline/fence artifact from the first job is
[mitigation-study-gh40155579729-1](https://github.com/s2002kumar/crashcourt/releases/download/evidence-2026-10/mitigation-study-gh40155579729-1.zip) (GitHub artifact 12584876230),
GitHub-reported SHA-256 **3697fb80e3f604f46561016bf772e25c00c351c8793b8dcda09b68e211419522**.
These hashes describe the GitHub artifact ZIPs, which the evidence release preserves byte for
byte; they are not independently reconstructed per-trial hashes. The two jobs' recorded
environment fingerprints were compared batch by batch and are identical.

## Overhead measurement: separate from the fault study

The mitigation-overhead benchmark exercises fault-free W1 approval runs under four arms
(base, F, I, FI). Its measurements do not count as N8 experimental batches. It times the
real async Postgres saver, ownership claims, connection epoch binding, ledger operations,
and throughput under controlled ordering, preserving raw durations and runner information.

The approved canonical measurement completed on GitHub-hosted Ubuntu 24.04:
[benchmark run 40278406294](https://github.com/s2002kumar/crashcourt/actions/runs/40278406294),
at source commit **33d0b4ff3f4104388a8d8a8176f8720f58b19ef7**.
Its benchmark record reported **checks: all passed** and published every duration.

| Round | Base median | Fence median | F vs base | Idempotency median | Both median | FI vs base |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 14.46 ms | 17.52 ms | +21.1% | 14.58 ms | 17.58 ms | +21.6% |
| 2 | 14.57 ms | 17.59 ms | +20.7% | 14.51 ms | 17.65 ms | +21.2% |
| 3 | 14.50 ms | 17.61 ms | +21.5% | 14.52 ms | 17.57 ms | +21.2% |

Each latency arm used **300 timed W1 units per round** after warm-up, under a seeded
interleaving. Fencing's median overhead was consistent across the three rounds;
idempotency alone was close to baseline. The precise figure is environment- and
workload-specific, not a universally expected cost.

**Tail latency and throughput are inconclusive.** Fence-versus-baseline p99 differed by
-3.7%, +121.5%, and +80.4% across the three rounds. Its measured throughput difference
ranged from **+39.5% to -20.3%**, showing contention/noise rather than a demonstrated
throughput improvement or a stable regression. Three rounds are not a confidence interval,
and no interval is claimed. The runner and complete distributions are preserved in
[bench-gh40278406294-1](https://github.com/s2002kumar/crashcourt/releases/download/evidence-2026-10/bench-gh40278406294-1.zip) (GitHub artifact 12631855036),
GitHub-reported SHA-256
**1f0a422bbd6737a7cfdda0239cafadcc8e470e5626261b00ad37462fd1203d68**.

An earlier laptop development run suggested a 16–17% median overhead; it is superseded as
the headline by this canonical runner measurement. No overhead observation changes a
formal fault experiment's result or frozen prediction.

## Evidence integrity and reproduction

Every formal trial records the declared fault placement, worker and client event histories,
the external-effect ledger, a direct repeatable-read Postgres snapshot, and the original
frozen-checker results. Re-evaluating the checker on archived records has produced byte-identical
outputs. The study gate requires the pinned experimental commit and valid instrument cells;
it does not permit new predictions to be inserted after the outcome is known.

To reproduce an existing formal result with the currently public tooling, see
[the repository quickstart and evidence commands](../README.md#inspect-the-formal-evidence).
The study artifact's own run metadata is the authority for the exact commit, environment,
trial counts, and worker history. GitHub Actions artifacts are temporary. Their original ZIP bytes, source-run identifiers,
and SHA-256 checksums are preserved in the published
[evidence-2026-10 release](https://github.com/s2002kumar/crashcourt/releases/tag/evidence-2026-10).

## Interpretive limits

- The runtime target is **only** LangGraph/Postgres at the pinned versions and sync durability.
- The effect workload and fault schedule are synthetic and deterministic, not sampled
  production incidents. Twenty out of twenty does not mean a 100% production failure rate.
- LangGraph does not promise concurrent execution on the same thread in this setup. These
  anomalies are **documentation GAPs**, not evidence of a violated documented guarantee.
- An ownership fence rejects stale checkpoint writes only after ownership changes; it does
  not undo effects that committed before takeover.
- Idempotent application and accurate mapping of a logical operation to a particular
  effect *attempt* are separate properties.
- Formal outcomes, their prediction mismatches, and benchmarking results must be
  reported separately. No run may be silently retried or altered to improve a headline.

## Related work and credits

- [langgraph#8039](https://github.com/langchain-ai/langgraph/issues/8039):
  external effect and crash/retry ordering that K1a replicates.
- [langgraph#8796](https://github.com/langchain-ai/langgraph/issues/8796):
  stale concurrent checkpoint writer behavior that K3 replicates.
- [CrashCourt pre-registration](preregistration.md):
  original experimental commitments, gate controls, and deviations.
- [CrashCourt README](../README.md):
  broader system architecture, other controls, reproducibility and limitations.

**V1 publication status (2026-10-26):** The four-way study and canonical benchmark are
completed, and the [checksummed evidence release](https://github.com/s2002kumar/crashcourt/releases/tag/evidence-2026-10)
was published with original run artifacts and an evidence manifest. The baseline/fence and
idempotency/combined arms ran in two jobs whose full recorded environment fingerprints are
identical; they are reported side by side, and the report pools no trials across them.
The registered checker mismatches, the resulting stopped jobs, and the post-outcome continuation
remain part of the scientific record. An independently reproduced external review has not
been claimed.
