# N8 mitigation study — operations record

This is a dated, post-run record. It is **not** the experiment's pre-registration
and does not amend the frozen checker, scenario hashes, predictions, or original
experimental commit. The binding commitments remain at
[4f4176bc185a0559db3b358ecbcbb7ae364c88d1](https://github.com/s2002kumar/crashcourt/commit/4f4176bc185a0559db3b358ecbcbb7ae364c88d1).

## Before the single-job study: 2026-10-19 to 2026-10-24

The pre-registered dispatch was `tranche.yml` once for the ten instrument cells, then
`experiment.yml` once per variant on that tranche's batches. Three runs came before the study
job; none produced a variant batch.

- **Tranche [39218496515](https://github.com/s2002kumar/crashcourt/actions/runs/39218496515),
  2026-10-19, commit 3e775a9.** Checker v1 and every observation came out as predicted in all ten
  instrument cells, N8-placebo's contract as predicted, and C9 passed. C1-w1-sync-fenced's
  contract differed in 50 of 50 trials on `acknowledged_is_head` and `fence_consistent`: the
  contract's evaluator took the op under test from `trial.json`'s alphabetical key order
  (`resume-1` before `start-1`). The fence had behaved as predicted. The evaluator was fixed in #46,
  which became the pre-registration commit 4f4176b; the pre-registration's Deviations record it.
  Not a qualifying run. Its artifact is in the evidence release.
- **Tranche [39260011909](https://github.com/s2002kumar/crashcourt/actions/runs/39260011909),
  2026-10-19, commit 4f4176b.** All ten instrument cells as predicted, both fence controls'
  contracts as predicted, C9 passed, on runner image 20261012.334.1 (kernel 6.17.0-1022-azure).
  Its artifact is in the evidence release.
- **Experiment [39312360471](https://github.com/s2002kumar/crashcourt/actions/runs/39312360471),
  2026-10-19, N8-base on tranche 39260011909.** The gate authorized it; `crashcourt run` then
  refused before any trial, because the runner (image 20261018.339.1, a pre-release then rolling
  out) had kernel 7.0.0-1017-azure where the instrument batches had 6.17.0-1022-azure. No batch
  directory and no trial; it is not in any outcome denominator. Its artifact
  (`experiment-N8-base-gh39312360471-1`, GitHub artifact 12217210708, SHA-256
  `9c14cbf75905042aab0477712b679b226e1afaa21e3d65959175a8371158dd3a`) holds only the gate's
  output and the refusal, is not in the evidence release, and expires on 2027-01-17.

The new image did not settle: on 2026-10-22 its release was withdrawn, a second pre-release
started, and public ubuntu-24.04 jobs started in the same second were landing on both images. So
instead of waiting longer, the study was amended once (#48, workflow-only) to run the ten
instrument cells and the variants in one job at 4f4176b, so that they share one runner by
construction, with a stop before the next variant at the first one not as predicted.

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

The two jobs' recorded environments were compared on 2026-10-26, batch by batch, with the same
fingerprint function the gate and the aggregator use. All 24 batches of both jobs (the ten
instrument batches of each, N8-base, N8-F, N8-I, and N8-FI) have one environment fingerprint,
`4f9a9e6155b64ce00678f8a516271bc9023e600e0a0572e2b5867f130b273235`: the same runner image (ubuntu24 20261023.334.3), kernel (6.17.0-1022-azure), Docker 28.0.4
and Compose 2.38.2, pinned container images, locked packages, libpq 18.6, and host (4 CPUs, 16
GiB). What differs is outside the fingerprint by design: each manifest's generation time and sink
clock offset, the run identifiers and dates, the workflow-definition commit (95e240a, then 43fd22d
with PR #50's `variants` input), and container uptimes. The four variants therefore ran in one
recorded environment. They are still two jobs on two runners a day apart, the second started after
the first's outcome was known, and each variant is its own 20-trial batch, reported side by side;
nothing is pooled.

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
