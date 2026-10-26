# CrashCourt in five minutes

**A reliability lab for durable AI-agent workflows: real fault injection, independent evidence, and reproducible claims.**

> **Engineering question:** When a worker crashes or loses ownership mid-workflow, does the next worker recover the saved state **and** avoid changing the outside world twice?

A checkpoint can look healthy while a customer is charged twice. A replacement worker can return success while the older worker later rewrites the saved head. CrashCourt makes those races reproducible with real worker processes and the LangGraph Postgres saver, then checks the saved checkpoints against a separate record of external effects.

**What I built:** a Python CLI, Linux process orchestrator, audited saver failpoints, independent hash-chained SQLite effect ledger, direct PostgreSQL snapshotter, frozen 14-invariant checker, git-pinned pre-registration gate, mitigation experiments, benchmark, and static evidence viewer. The checker does not trust the runtime's own success response. It evaluates recorded causal events against cited documentation.

[Source code](../src/crashcourt/) · [Technical architecture](ARCHITECTURE.md) · [Research report](../research/n8-report.md) · [Reproduce a verified experiment](REPRODUCING.md) · [Permanent evidence release](https://github.com/s2002kumar/crashcourt/releases/tag/evidence-2026-10)

## The experiment at a glance

Consider a charge operation interrupted before all of its checkpoint writes have committed:

1. **A** applies an external charge and is held at precisely named saver calls.
2. **B** receives the redelivered work, runs, and acknowledges success to the client.
3. **A** is released. CrashCourt inspects the checkpoint lineage, the chosen head, the ledger, and the client acknowledgment separately.

The original N8 experiment composes two previously reported failure mechanisms. It is an original **combination and mitigation investigation**, not a claim to have discovered LangGraph's underlying behaviors.

| Treatment | Forked checkpoint lineage | The acknowledged run remains head | Applied charges | Applied-attempt identity in saved state |
| --- | --- | --- | --- | --- |
| Baseline | 20/20 | 0/20 | 2 per trial | Not claimed |
| Epoch fence (F) | 0/20 | 20/20 | 2 per trial | Not claimed |
| Idempotent sink (I) | 20/20 | 0/20 | 1 per trial | Consistent, 20/20 |
| Fence + idempotency (FI) | 0/20 | 20/20 | 1 per trial | **Inconsistent, 20/20** |

Each treatment has 20 valid trials. Baseline and F were recorded in the original study; I and FI were recorded in a separate, explicitly disclosed continuation. The experiments must not be pooled or described as one matched environment without confirming complete environment fingerprints.

**The engineering takeaway:** A fence decides **who may write durable state**. Idempotency decides **how many times a logical external effect applies**. Using both can still leave the wrong effect-attempt reference in the saved state. These are different correctness properties.

All four treatments met their independently specified mitigation-contract predictions. The frozen checker **did not** match its registered I6a prediction in F and FI; the workflow stopped, preserved the evidence, and documented the post-outcome continuation. No documented LangGraph guarantee violation is claimed: this concurrent same-thread schedule is outside the pinned runtime's documented guarantees.

[Original study](https://github.com/s2002kumar/crashcourt/actions/runs/40155579729) · [Continuation, including its recorded stop](https://github.com/s2002kumar/crashcourt/actions/runs/40276230146) · [Predictions and deviations](../research/preregistration.md)

## Performance is measured separately

On GitHub-hosted Ubuntu, three fault-free benchmark rounds of **300 timed W1 units per treatment per round** measured an ownership-fence median overhead of **20.7–21.5%** compared with baseline. Idempotency alone stayed near baseline. The p99 and throughput measurements were too variable to support a stable comparison.

This is the measured cost of a *particular* W1 run and runner configuration, not a universal overhead number. The benchmark is not a formal fault-experiment cell and cannot change a prediction or verdict.

[Canonical benchmark run and raw distributions](https://github.com/s2002kumar/crashcourt/actions/runs/40278406294) · [Measurement method and limitations](../research/n8-report.md#overhead-measurement-separate-from-the-fault-study)

## What makes this systems engineering rather than a simulated test

- **Real persistence:** LangGraph's real async Postgres saver and a pinned PostgreSQL environment, not a mocked storage layer.
- **Controlled concurrency:** POSIX process control, deterministic event barriers, and failpoints at audited saver calls rather than arbitrary sleeps.
- **Independent observation:** a separately committed effect ledger, per-process histories, and direct repeatable-read snapshots of checkpoint tables.
- **Fail-closed research:** pre-registered scenarios and expected results, instrument controls, immutable checker hashes, verification against git history, retained unsuccessful runs, and explicit protocol deviations.
- **Reproduction:** versioned CLI commands, reproducibility guide, automated CI, and a checksummed evidence release containing original experiment and benchmark artifacts.

**Where to inspect implementation:** [orchestrator](../src/crashcourt/orchestrator/) · [failpoint saver](../src/crashcourt/failpoints/) · [effect ledger](../src/crashcourt/sink/ledger.py) · [fencing triggers](../src/crashcourt/mitigation/fence.py) · [checker](../src/crashcourt/checker/) · [tests](../tests/)

## See the evidence yourself

For a quick independent inspection, start with the [published, SHA-256-verified release](https://github.com/s2002kumar/crashcourt/releases/tag/evidence-2026-10). The release contains the recorded trial artifacts, the source-run manifest, and checksums; **a green badge is not the scientific verdict**. Follow the [reproduction guide](REPRODUCING.md) to verify the K1a result from a clean checkout or execute a comparable fault schedule on Linux. The K1a and K3 experiments replicate previously reported behaviors; N8 is the composed recovery investigation.

The [N8 report](../research/n8-report.md) shows exact four-way observations, prediction mismatches, benchmark methodology, and limitations. The [evidence viewer](../site/) currently uses clearly labeled **demo fixtures**; do not mistake them for hosted formal-result pages.

## Boundaries and next direction

CrashCourt V1 measures **LangGraph 1.2.12 + Postgres saver 3.1.2 + Postgres 18.6**, under sync durability, using synthetic workloads and controlled single-host schedules. It does not estimate production incident rates, emulate host power loss, validate other runtimes, or establish new upstream bugs. A second runtime (DBOS candidate) and independent external adoption are future work—not implemented capabilities.

**If you have 90 seconds:** read the treatment table above, then look at the [N8 report](../research/n8-report.md#verified-four-way-mitigation-comparison). **If you have 10 minutes:** follow the [reproduction guide](REPRODUCING.md) and check the original evidence archive.
