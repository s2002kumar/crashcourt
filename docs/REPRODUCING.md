# Reproduce CrashCourt's verified experiment

This guide targets the pinned **K1a** replication: a crash after an external charge but before pending writes are committed, followed by redelivery to another worker. The original registered result was 20 valid trials, all with duplicate charges. This result was first reported upstream; reproducing it is instrument validation, not a new upstream bug.

Two kinds of reproduction are deliberately separate:

- **Evidence reproduction (no Docker):** verify the existing formal run and rerun the unchanged checker over its saved artifacts.
- **Live fault reproduction (Linux):** execute a corresponding controlled development schedule on the local stack. A live development run is not a pre-registered formal batch and must not be described as one.

## Prerequisites

Python is installed through uv 0.12.1 from the locked project. Evidence reproduction needs Git, GitHub CLI, `unzip` and a SHA-256 utility (`sha256sum` in the commands below); no Docker is required. For Windows, run the shell commands in WSL2 or a compatible Unix shell. Live fault reproduction additionally needs Linux/WSL2 and Docker Compose.

## A. Verify the formal evidence

From a fresh checkout, obtain the **preserved release** instead of relying on a 90-day Actions artifact:

~~~bash
git clone https://github.com/s2002kumar/crashcourt.git
cd crashcourt
uv sync --locked --group dev
mkdir -p ../cc-release ../cc-evidence/k1a/experiment-K1a-gh38964246590-1

gh release download evidence-2026-10 --repo s2002kumar/crashcourt \
  --pattern 'SHA256SUMS.txt' \
  --pattern 'experiment-K1a-gh38964246590-1.zip' \
  --dir ../cc-release

(cd ../cc-release && sha256sum --ignore-missing -c SHA256SUMS.txt)
unzip -q ../cc-release/experiment-K1a-gh38964246590-1.zip \
  -d ../cc-evidence/k1a/experiment-K1a-gh38964246590-1

K1A="../cc-evidence/k1a/experiment-K1a-gh38964246590-1/runs/K1a-gh38964246590-1"
uv run crashcourt experiment verify "$K1A"
~~~

The release also provides `evidence-manifest.json`, which links archived ZIPs to their original
GitHub workflow runs and artifact identities. The digest check compares the download to the
published `SHA256SUMS.txt`; it does not replace examining the run's pre-registration and verified
trial records. If using an expiring Actions artifact instead, the equivalent historical command is
`gh run download 38964246590 --repo s2002kumar/crashcourt --dir ../cc-evidence/k1a`.


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

The reproduction must run under Linux because the harness controls actual worker processes and POSIX signals. Start from the clone. If you just ran the checker comparison above, you are in `../cc-k1a-checker`, so the first command returns to the clone:

~~~bash
cd ../crashcourt
docker compose up -d --wait --build
uv run crashcourt doctor
uv run crashcourt faults selftest --trials 20 --report ../cc-fault-selftest.md
docker compose up -d --wait postgres
uv run crashcourt run tests/integration/scenarios/t11-k1a-shape.yaml --trials 20 --out ../cc-k1a-development
~~~

The self-test restarts Postgres. The explicit health wait before the development batch prevents a false refusal while Compose still marks the container as starting.

The stack publishes Postgres on 127.0.0.1:55432, Toxiproxy on 8474 and 15432-15434, and the effect sink on 8480. If a local Postgres already holds 55432, publish the stack's Postgres on another port with a Compose override (`ports: !override ["127.0.0.1:55462:5432"]` for the `postgres` service, added to `COMPOSE_FILE`) and set `CRASHCOURT_PG_DSN=postgresql://crashcourt:crashcourt@127.0.0.1:55462/crashcourt`. Keep the Compose project name `crashcourt`: the database-restart fault acts only on that project's container.

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

GitHub Actions artifacts expire, so prefer the [published, checksummed V1 evidence release](https://github.com/s2002kumar/crashcourt/releases/tag/evidence-2026-10) for long-lived reproduction. The release preserves the original ZIP bytes and an origin manifest; these should still be verified against the recorded run and checker before trusting a result.

The formal K1a result is not an estimate of production failure frequency; it demonstrates a behavior deterministically under a particular adversarial schedule. Differences in kernel, runner image, Postgres binaries, or upstream dependencies must be recorded, not silently pooled.
