# N8 mitigation study — operations record

This is a dated, post-run record. It is **not** the experiment's pre-registration
and does not amend the frozen checker, scenario hashes, predictions, or original
experimental commit. The binding commitments remain at
[4f4176bc185a0559db3b358ecbcbb7ae364c88d1](https://github.com/s2002kumar/crashcourt/commit/4f4176bc185a0559db3b358ecbcbb7ae364c88d1).

## Original study: 2026-10-25

[GitHub Actions 40155579729](https://github.com/s2002kumar/crashcourt/actions/runs/40155579729)
ran the ten instrument batches, aggregation and C9 control, all of which qualified.
It then ran N8-base and N8-F in the same workflow job. Baseline produced forks
and two applied charges in 20/20. With the ownership fence, stale writes were
blocked, no forks survived, the acknowledged run was the saved head, and
two charge effects were still applied in 20/20.

N8-F's independent mitigation contract matched the pre-registration. Frozen
checker v1's I6a classified the head as GAP in 20/20 trials, however, outside
the registered PASS/NO_ANOMALY_UNSPECIFIED alternatives. Under the original
stop rule, the job **stopped before N8-I and N8-FI**. No trials from those
variants were present in the first run.

## Authorized continuation after seeing the original result: 2026-10-26

The continuation was approved **after** the N8-F I6a discrepancy was seen,
so it is a disclosed deviation from the study's single-job uninterrupted
dispatch plan. Workflow-only PR #50 selected exactly N8-I and N8-FI and retained
the experiment checkout commit, all predictions, gates, and stop conditions.

[GitHub Actions 40276230146](https://github.com/s2002kumar/crashcourt/actions/runs/40276230146)
ran 10 instrument batches, which were all as predicted, and C9, which passed.
The workflow then ran N8-I and N8-FI in that order, each 20/20 valid trials.
It did **not** rerun the original baseline or fence batches.

- N8-I: 20/20 fork, 0/20 acknowledged run as saved head, 20/20 logically
  EXACTLY_ONCE applied effects, 20/20 CONSISTENT applied-attempt reference;
  contract and frozen checker aggregate both as predicted.
- N8-FI: 0/20 fork, 20/20 acknowledged run as saved head, 20/20 logically
  EXACTLY_ONCE applied effects, 20/20 INCONSISTENT applied-attempt reference;
  contract as predicted but frozen checker v1 I6a GAP in 20/20, contrary to
  the unchanged registered alternatives. The workflow stopped and marked
  its job as failed, as the stop rule requires.

The I6a discrepancy stems from interpreting overlapping live dispatches:
the frozen rule labels the thread's head anomalous whenever A and B overlap,
even when stale A's writes are rejected. The original prediction was derived
from K1a's killed-then-redelivered schedule. This interpretation was apparent
after N8-F and is stated as a limitation, not a discovery or guarantee violation.

These two Github-hosted jobs have separately captured environment manifests.
Until a full fingerprint comparison confirms their equality, the four variants
must be reported side-by-side, **not pooled as one environment**.

### Archived continuation artifacts

- Study run: 40276230146
- Artifact: mitigation-study-gh40276230146-1
- GitHub artifact ID: 12630983255
- GitHub-reported SHA-256: 56d4d83c376c390404e8543b05ca84a2ea7ce5276273c05853b75d974a108deb
- Experimental checkout commit: 4f4176bc185a0559db3b358ecbcbb7ae364c88d1

## Separate canonical overhead measurement: 2026-10-26

[GitHub Actions 40278406294](https://github.com/s2002kumar/crashcourt/actions/runs/40278406294)
completed the fault-free four-arm mitigation benchmark at
commit 33d0b4ff3f4104388a8d8a8176f8720f58b19ef7.
The benchmark record marked all consistency checks as passed.
The benchmark is **not** a new evidence cell of the formal N8 study.

Three rounds used 300 timed W1 units per arm per round, plus warmup;
median F-over-baseline latency overhead was +21.1%, +20.7%, +21.5%.
FI-over-baseline median overhead was +21.6%, +21.2%, +21.2%.
Idempotency alone stayed near baseline. The p99 and throughput comparisons
were variable and do not support a stable estimate from these three rounds.

- Artifact: bench-gh40278406294-1
- GitHub artifact ID: 12631855036
- GitHub-reported SHA-256: 1f0a422bbd6737a7cfdda0239cafadcc8e470e5626261b00ad37462fd1203d68

Full evidence and limitations are in [n8-report.md](n8-report.md).
The definitive raw evidence is the pinned source-run artifacts, additionally preserved
in the [published checksummed release](https://github.com/s2002kumar/crashcourt/releases/tag/evidence-2026-10).
This prose record is a guide to those primary records, not a replacement for them.
