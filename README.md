# Crashcourt

An open-source conformance and reliability lab for durable agent execution.

**Status: pre-results.** Nothing has been measured yet, so this repository makes no claims.

Crashcourt will crash, freeze, and partition the persistence layers under agent runtimes (starting with
LangGraph checkpointers on real Postgres), then judge each outcome against the runtime's documented
guarantees and an independent record of real-world effects. Every verdict will carry its evidence, and
every failure will ship as a standalone repro.

## Quickstart (environment only, T-01)

```bash
git clone <repo> && cd crashcourt
uv sync --locked --group dev        # installs uv's build of Python 3.12.13 and the locked packages
docker compose up -d --wait         # Postgres :55432, Toxiproxy API :8474, effect sink :8480 (127.0.0.1)
uv run crashcourt doctor            # checks the pinned environment, writes manifest.json
```

Images are pinned by digest in compose.yaml. After changing an image tag, run
`python scripts/pin_images.py` to re-pin.

## Running trials (Linux only; WSL2 on Windows)

`crashcourt run` spawns two worker processes, drives them over Unix sockets, and kills or stops
them with POSIX signals, so it runs only on Linux and refuses to start anywhere else. On Windows,
run it inside WSL2 (with Docker Desktop's WSL integration turned on), from a clone on the Linux
filesystem rather than under `/mnt/c`:

```bash
# in a WSL2 shell, with uv 0.12.1 installed (the version pyproject.toml requires)
git clone <repo> && cd crashcourt
uv sync --locked --group dev
docker compose up -d --wait --build                    # the sink is built from this checkout
uv run crashcourt run scenarios/smoke.yaml --trials 5  # writes runs/<batch>/
```

Each batch directory holds the manifest, the batch record, and `run.json` (seeds, each worker's
Toxiproxy route, the sink's health and clock offset). Each trial directory holds every process's
history, `merged.jsonl` (all of them in causal order), and `trial.json` (whether the trial can be
trusted as evidence, and why not). `--canonical` marks a batch meant for publication: it also
refuses a dirty checkout and a sink clock more than one second from the host's. WSL2 and Docker
Desktop share one kernel, so the offset there is near zero; under a separate VM it is not.

## Evidence explorer (`site/`)

A static site that renders the harness's artifacts and computes no verdicts. It needs Node 24
(`site/.node-version`).

```bash
cd site
npm ci
npm run build                       # static export in site/out, from the demo data in site/fixtures
npm run test:unit
npx playwright install chromium && npm run test:e2e
npm run build:release               # release build: reads ../results only, behind the release guard
```

Until real batches exist, a development build renders labeled demo data (`uv run crashcourt fixtures
--demo` regenerates it): every id starts with `DEMO-`, every record says `provenance: "fixture"`, and every
page that shows it carries a "Demo data — not a result" banner. A release build reads only harness batches
in `results/` and fails on any fixture, on any `DEMO-` id, and when `results/` holds no batch.

`site/DESIGN.md` describes the visual system and `site/BUDGETS.md` the JavaScript budgets.

## Related work and credits
- langchain-ai/langgraph#8796: write-ownership conformance proposal and SIGSTOP fork repro (bharatnpti).
- langchain-ai/langgraph#8039, #8234: sync-durability persistence ordering and post-crash recovery.
- langchain-ai/langgraph#8764: crash before the first durable checkpoint.
- mstevens843/crashpoint: crash-to-effect exactly-once measurements across five durable-execution engines.
- xbstack repros: independent fork and admission-ledger reproductions.
- AgentCI: evidence and authority fixtures for agent systems.

## License
MIT (see LICENSE).
