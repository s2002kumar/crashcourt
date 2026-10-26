# Reproduce CrashCourt's verified experiment

This guide targets the pinned **K1a** replication: a crash after an external charge but before pending writes are committed, followed by redelivery to another worker. The original registered result was 20 valid trials, all with duplicate charges. This result was first reported upstream; reproducing it is instrument validation, not a new upstream bug.

Two kinds of reproduction are deliberately separate:

- **Evidence reproduction (no Docker):** verify the existing formal run and rerun the unchanged checker over its saved artifacts.
- **Live fault reproduction (Linux):** execute a corresponding controlled development schedule on the local stack. A live development run is not a pre-registered formal batch and must not be described as one.

## Prerequisites

Python is installed through uv 0.12.1 from the locked project. Evidence reproduction needs Git and GitHub CLI; artifact downloads require GitHub access. Live fault reproduction additionally needs Linux/WSL2 and Docker Compose.

## A. Verify the formal evidence

From a fresh checkout:

~~~bash
git clone https://github.com/s2002kumar/crashcourt.git
cd crashcourt
uv sync --locked --group dev
mkdir -p ../cc-evidence/k1a
gh run download 38964246590 --repo s2002kumar/crashcourt --dir ../cc-evidence/k1a

K1A="../cc-evidence/k1a/experiment-K1a-gh38964246590-1/runs/K1a-gh38964246590-1"
uv run crashcourt experiment verify "$K1A"
~~~

The verification command checks the historical checkout, registered scenario and predictions,
frozen checker hashes and authorization record. It does not depend on trusting a README count.

To recompute and compare checker outputs without modifying the current checkout, use a detached worktree at the run's frozen commit:

~~~bash
git worktree add --detach ../cc-k1a-checker 6e56791
cd ../cc-k1a-checker
uv sync --locked --group dev
uv run crashcourt experiment classify --compare ../cc-evidence/k1a/experiment-K1a-gh38964246590-1/runs/K1a-gh38964246590-1
uv run crashcourt experiment aggregate ../cc-evidence/k1a/experiment-K1a-gh38964246590-1/runs/K1a-gh38964246590-1 --out ../cc-evidence/k1a-cells.json
~~~

The compare command requires the checker to produce byte-identical results. A failure must be investigated, not relabeled or discarded. Neither command overwrites the archived histories or ledger.

## B. Execute the controlled development schedule

The reproduction must run under Linux because the harness controls actual worker processes and POSIX signals:

~~~bash
cd crashcourt
docker compose up -d --wait --build
uv run crashcourt doctor
uv run crashcourt faults selftest --trials 20 --report ../cc-fault-selftest.md
docker compose up -d --wait postgres
uv run crashcourt run tests/integration/scenarios/t11-k1a-shape.yaml --trials 20 --out ../cc-k1a-development
~~~

The self-test restarts Postgres. The explicit health wait before the development batch prevents a false refusal while Compose still marks the container as starting.

The development scenario has the same K1a-shaped fault path, but its different scenario identity and seed mean the run is **development evidence**, not the original formal K1a result. Follow the [README's classify instructions](../README.md#run-trials-linux-only) to inspect its resulting batch; do not aggregate it with the pre-registered run.

## What to examine

In each trial directory, inspect:

- The **orchestrator events**: the declared hold or SIGKILL, redelivery and client outcome.
- The **effect ledger**: the independent record of attempted and applied charges, and the hash chain.
- The **Postgres snapshot**: checkpoint lineage and pending writes, not merely the runtime API's returned state.
- The **checker records**: invariant verdict, documentation classification, and evidence pointers.
- The **authorization and manifest**: the commit, environment fingerprint, frozen inputs and gate decision.

For context, see [architecture](ARCHITECTURE.md), [N8 technical report](../research/n8-report.md) and the [pre-registration](../research/preregistration.md).

## Limitations and preservation

GitHub Actions artifacts expire. Do not assume the download commands will remain available after their retention period. A durable evidence release with checksums must be published and its raw records verified before treating the public reproduction path as long-lived.

The formal K1a result is not an estimate of production failure frequency; it demonstrates a behavior deterministically under a particular adversarial schedule. Differences in kernel, runner image, Postgres binaries, or upstream dependencies must be recorded, not silently pooled.
