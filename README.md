# Crashcourt

An open-source conformance and reliability lab for durable agent execution.

**Status: pre-results.** Nothing has been measured yet, so this repository makes no claims.

Crashcourt will crash, freeze, and partition the persistence layers under agent runtimes (starting with
LangGraph checkpointers on real Postgres), then judge each outcome against the runtime's documented
guarantees and an independent record of real-world effects. Every verdict will carry its evidence, and
every failure will ship as a standalone repro.

## Related work and credits
- langchain-ai/langgraph#8796: write-ownership conformance proposal and SIGSTOP fork repro (bharatnpti).
- langchain-ai/langgraph#8039, #8234: sync-durability persistence ordering and post-crash recovery.
- langchain-ai/langgraph#8764: crash before the first durable checkpoint.
- mstevens843/crashpoint: crash-to-effect exactly-once measurements across five durable-execution engines.
- xbstack repros: independent fork and admission-ledger reproductions.
- AgentCI: evidence and authority fixtures for agent systems.

## License
MIT (see LICENSE).
