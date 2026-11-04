# Independent review of CrashCourt V1

This is an invitation to inspect or reproduce a bounded result, **not** a claim that an independent review has happened. No outside reproduction or adoption is established.

## First 10 minutes

1. Read the [five-minute case study](ENGINEERING_CASE_STUDY.md) for the distinction between checkpoint lineage and effects.
2. Inspect the [frozen N8 report](../research/n8-report.md), including the failed I6a predictions and the explicitly disclosed continuation.
3. Follow the [reproduction guide](REPRODUCING.md), section **A**, from a clean clone. It checks the original published, checksummed K1a evidence. This verifies an earlier documented failure mechanism; it is not a new upstream LangGraph bug.
4. Inspect the [orchestrator](../src/crashcourt/orchestrator/), [effect ledger](../src/crashcourt/sink/ledger.py), [checker](../src/crashcourt/checker/) and [fencing mitigation](../src/crashcourt/mitigation/fence.py). Ask what could escape each observer.
5. If you have a Linux/WSL2 Docker setup, follow section **B** of the reproduction guide for a new **development** run, then compare its observations without pooling it with the historical formal batch.

## Questions worth challenging

- Does the independent effect ledger really record applied external effects, rather than just the worker's acknowledgement?
- What does an epoch fence prevent that an idempotency key does not, and vice versa?
- Are the combined-protection attempt-reference mismatches independently observable?
- Does the frozen checker reject malformed or contradictory evidence?
- Which pinned version, durability setting, fault schedule, workload and host conditions are necessary to reproduce an observation?
- Are failures within LangGraph's documented guarantees, or outside those guarantees?

## Reporting a reproduction

Please report **what you actually ran**, including commit SHA, OS, runtime and database versions, command and outputs, SHA-256 check, and whether the result matched. Link an issue or public PR if you consent to it being shared. Failed or partial attempts are useful; no outcome should be relabeled as passing.

**Current boundary:** Synthetic, scripted-model workloads; LangGraph 1.2.12, Postgres saver 3.1.2, PostgreSQL 18.6 and sync durability. Results describe controlled schedules, not production incident rates. No independent external run has yet been recorded here.
