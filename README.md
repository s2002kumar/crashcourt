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
docker compose up -d --wait         # Postgres on 127.0.0.1:55432, Toxiproxy API on 127.0.0.1:8474
uv run crashcourt doctor            # checks the pinned environment, writes manifest.json
```

Images are pinned by digest in compose.yaml. After changing an image tag, run
`python scripts/pin_images.py` to re-pin.

## Related work and credits
- langchain-ai/langgraph#8796: write-ownership conformance proposal and SIGSTOP fork repro (bharatnpti).
- langchain-ai/langgraph#8039, #8234: sync-durability persistence ordering and post-crash recovery.
- langchain-ai/langgraph#8764: crash before the first durable checkpoint.
- mstevens843/crashpoint: crash-to-effect exactly-once measurements across five durable-execution engines.
- xbstack repros: independent fork and admission-ledger reproductions.
- AgentCI: evidence and authority fixtures for agent systems.

## License
MIT (see LICENSE).
