# N8: Stale-worker recovery, checkpoint ownership, and external effects

**Status:** Evidence-backed interim technical report. The N8-I and N8-FI continuation and the
canonical mitigation-overhead measurement are not included until those runs complete and their
artifacts are verified. This document does **not** claim a violation of any documented LangGraph
guarantee.

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
This is an experimentally controlled demonstration of a mitigation boundary: database ownership
fencing protects the checkpoint lineage but cannot retract an external effect sent before
takeover. It is not an estimate of incident frequency in production.

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
langgraph-checkpoint-postgres 3.1.2, Postgres 18.6, and \`durability="sync"\`.

N8 is CrashCourt's composition of two previously known recovery mechanisms. K1a replicates a
reported duplicate external effect; K3 replicates a reported concurrent-writer checkpoint fork.
The component failures are **not** claimed as novel discoveries.

## Current verified mitigation comparison

| Observation per 20-trial cell | N8-base: no protection | N8-F: epoch fence |
| --- | ---: | ---: |
| Checkpoint lineage fork | 20/20 | 0/20 |
| Acknowledged run is the stored head | 0/20 | 20/20 |
| Stale worker's late writes rejected | 0/20 | 20/20 |
| Applied charges per trial | 2 | 2 |
| INVALID trials | 0 | 0 |
| Documented-guarantee violation candidates | 0 | 0 |

The 10 instrument batches qualified, the C9 control passed, and both fence-control contracts
matched their predictions. The two variants above are preserved in
[Actions run 40155579729](https://github.com/s2002kumar/crashcourt/actions/runs/40155579729),
along with the environment fingerprint and individual trial artifacts. The original standalone N8
result is [run 39061196254](https://github.com/s2002kumar/crashcourt/actions/runs/39061196254).

The sample size is for controlled schedule reproducibility, not statistical estimation of how
frequently crashes or duplicates occur in production.

### A pre-registration mismatch was preserved, not repaired

The frozen v1 checker's I6a outcome for **N8-F** was **GAP in 20/20 trials**, whereas the
pre-registered prediction expected PASS or NO_ANOMALY_UNSPECIFIED. The pre-registration inferred
that prediction from K1a, where A had been killed before B started. In N8-F, A and B were both
dispatched on the same thread while A was alive. The frozen I6a rule regards the head of a thread
with overlapping dispatches as an anomaly regardless of which worker saved that head; the
documentation disclaimer therefore classifies it as a GAP.

The separate mitigation contract reported N8-F's protection as predicted. That is **not**
permission to reinterpret or modify the frozen checker. The disagreement was disclosed and the
original study stopped as pre-registered. A subsequent decision to continue with the remaining
variants is a deviation made **after observing N8-F**, and must be kept in the operations record,
workflow metadata, and pre-registration deviations section.

### Additional registered variants, not yet observed in this report

- **N8-I:** idempotent external-effect ledger without checkpoint ownership fencing.
- **N8-FI:** both ownership fencing and idempotent effect application.

Their predictions remain whatever was locked before the formal experiments. In particular, the
combined treatment could prevent a second *applied* charge while retaining an incorrect mapping
between the saved attempt identifier and the attempt that originally applied the effect. That is a
**pre-registered hypothesis, not an observed result** until the continuation artifacts are
verified. The I6a prediction mismatch already seen in N8-F must remain visible in the N8-FI
comparison; it cannot be relabeled after the fact.

The continuation runs its own instrument gate. Results from different GitHub runner
fingerprints must be reported as separate environments, even with identical checked-out
experimental code. **Do not pool trials across differing environment fingerprints.**

## Overhead measurement: separate from the fault study

The mitigation-overhead benchmark exercises fault-free W1 approval runs under four arms
(base, F, I, FI). Its measurements do not count as N8 experimental batches. It times the
real async Postgres saver, ownership claims, connection epoch binding, ledger operations,
and throughput under controlled ordering, preserving raw durations and runner information.

One development run on a laptop saw an approximately 16–17% increase in median W1 duration
with fencing across three rounds. This is **non-canonical**. The laptop's p99 and throughput
figures were not stable enough to support a comparison. No reproducible runner-based cost
claim belongs in the conclusions until the approved canonical CI run completes.

## Evidence integrity and reproduction

Every formal trial records the declared fault placement, worker and client event histories,
the external-effect ledger, a direct repeatable-read Postgres snapshot, and the original
frozen-checker results. Re-evaluating the checker on archived records has produced byte-identical
outputs. The study gate requires the pinned experimental commit and valid instrument cells;
it does not permit new predictions to be inserted after the outcome is known.

To reproduce an existing formal result with the currently public tooling, see
[the repository quickstart and evidence commands](../README.md#inspect-the-formal-evidence).
The study artifact's own run metadata is the authority for the exact commit, environment,
trial counts, and worker history. GitHub Actions artifacts are temporary until a separately
verified evidence release is published.

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

**Publication gate:** Add N8-I/N8-FI observed results, the recorded continuation deviation,
canonical benchmark outcome, comparison of runner fingerprints, stable evidence links, and
release checksums before declaring the full mitigation study or V1 closed.
