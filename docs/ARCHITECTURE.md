# CrashCourt architecture

**V1 target:** LangGraph 1.2.12, Postgres checkpointer 3.1.2, Postgres 18.6, synchronous durability. This laboratory tests the boundary between durable workflow state and externally committed side effects. It is not a general-purpose database consistency checker.

## System responsibilities and trust boundaries

| Layer | Responsibility | Trust boundary |
| --- | --- | --- |
| Experiment gate | Checks scenario hash, pinned commit, pre-registration, frozen checker, qualifying controls | A green CI badge alone is insufficient |
| Orchestrator | Drives crashes, barriers, redelivery and releases | Records the client's view independently of workers |
| Target adapter | Executes LangGraph through the real Postgres saver and a failpoint wrapper | No simulated persistence |
| Fault system | SIGKILL, held saver calls, network toxics, DB termination and restart | A fault at the wrong landmark invalidates a trial |
| Independent effect sink | Hash-chained external-effect ledger and attempted-charge counter | A runtime reporting success does not prove an effect happened |
| Snapshotter | Reads actual Postgres checkpoint and pending-write rows | The target API is not the source of truth |
| Frozen checker | Fourteen invariants, interpreted against cited documentation | A documentation GAP is not a violation |
| Mitigation contract | Epoch ownership, stale writes, acknowledged head and effect attempts | Cannot silently amend frozen predictions |
| Reporter and explorer | Render verified results | Presentation does not compute verdicts |

## Data flow

~~~mermaid
flowchart TD
    P["Frozen scenario + predictions"] --> G["Authorization gate"]
    G --> O["Orchestrator"]
    O --> A["Worker A: LangGraph + failpoint saver"]
    O --> B["Worker B: LangGraph + failpoint saver"]
    A --> PX["Toxiproxy / real Postgres saver"]
    B --> PX
    PX --> DB[("Postgres: checkpoints and pending writes")]
    A --> ES[("Independent effect ledger")]
    B --> ES
    O --> HI["Per-process event histories"]
    DB --> SN["Direct Postgres snapshot"]
    ES --> EV["Evidence bundle"]
    HI --> EV
    SN --> EV
    EV --> C["Frozen checker + separate mitigation contract"]
    C --> R["Report, trace and evidence explorer"]
~~~

A held operation identifies an audited saver landmark. The orchestrator releases it on a recorded event (for example, the replacement worker's acknowledgment), not on an arbitrary timer. This makes the adversarial schedule reproducible without claiming it occurs at any particular frequency in production.

## The N8 example

Worker A resumes an approval workflow and charges the external effect sink. A is then held before committing task writes and checkpoint state. Worker B is redelivered the same logical operation on the thread, performs the charge, returns success to the client, and only then is A released. CrashCourt checks separately whether the checkpoint lineage forks, which checkpoint is now selected as head, and how many charges the independent ledger applied.

In the original N8 baseline, a fork and two charges occurred in 20/20 trials. Adding an ownership fence prevents the stale checkpoint branch (0/20 forks) while two charges still applied in every trial. See [N8 technical report](../research/n8-report.md) for the evidence and methodological limits.

## Fencing vs idempotency vs attempt identity

A fencing dispatch claims a per-thread ownership epoch before reading thread state. The worker binds that epoch to each checked-out database connection. Postgres triggers reject stale or missing epochs on checkpoint and pending-write operations. Ownership changes are ordered with database write transactions using row locking.

The external-effect sink has an independent logical-operation idempotency mode: it records attempted requests while allowing only one applied charge per logical operation. A fence cannot undo a charge already committed before takeover. Equally, a single applied effect does not imply that a saved checkpoint points to the correct effect *attempt*. All three properties must be evaluated independently.

The mitigation model assumes trusted workers, not arbitrary code capable of forging a session epoch. It is not an authentication scheme.

## Scientific evidence contract

Each formal batch preserves its experimental commit, content-hashed scenario, gate authorization, environment fingerprint, worker histories, ledger, database snapshot, per-invariant evidence, and aggregate results.

The frozen checker evaluates only causal ordering supported by events. Incorrect fault placement or broken integrity renders a trial INVALID. A documented guarantee is required to classify an anomaly as VIOLATION_CANDIDATE; a definitive violation requires independent review.

Formal fault experiments, mitigation-contract results, and overhead benchmarks are distinct. The benchmark has no fault or checker and cannot repair failed predictions. Continuation after the registered stop is disclosed as a deviation.

## Engineering navigation

- [Run CrashCourt](../README.md#quickstart)
- [Verify formal evidence](../README.md#inspect-the-formal-evidence)
- [Study commitments and deviations](../research/preregistration.md)
- [Frozen checker implementation](../src/crashcourt/checker/)
- [Worker and orchestrator](../src/crashcourt/orchestrator/)
- [Ownership fence](../src/crashcourt/mitigation/fence.py)
- [Effect ledger](../src/crashcourt/sink/ledger.py)
- [Evidence explorer](../site/)
- [Tests](../tests/)

## Scope limits

V1 is one pinned LangGraph/Postgres configuration, two synthetic workloads, and carefully controlled single-host faults. It does not measure real-world incident frequency, actual power failures, arbitrary model behavior, cross-runtime guarantees, or prove that a new upstream software bug exists. The evidence should not be generalized beyond the tested conditions.
