# Gate note — G0 · 2026-10-09

1. What was built: research/source-audit.md, a source audit of the pinned LangGraph savers and the Pregel write path
   (langgraph 1.2.12, langgraph-checkpoint 4.2.0, -postgres 3.1.2, -sqlite 3.1.1), with file:line citations at the
   release tags; tests/audit/, which checks its claims against a real Postgres 18.6 (transaction boundaries, rollback,
   write-conflict rules, "latest" versus commit order, and the storage-level fork) and records SQLite's behavior; and
   research/evidence/t-03/ (the statement log, the upstream search, and the first-tick ordering experiment).

2. Why: G0 asks whether the transaction boundaries and the ambiguous-commit windows are located in code at the pinned
   tags. The network and database fault cells are built around those windows; a cell aimed at a window that does not
   exist would measure nothing. The fork itself was already established by #8796 and the xbstack repro, so here it is
   a replication, not a finding.

3. Important alternatives, and why they lost:
   - Rely on #8796 and the docs, and skip the audit. The docs do not say where a put commits, and #8796 covers one
     capability. Without the commit points, the lost-acknowledgment and reset cells would be guesses.
   - Test only, without reading the code. Tests show what happens in the cases written; the windows had to be found
     in the code first, and several claims (the pool path, the no-pipeline fallback) are read from code and marked so.
   - Read only, without tests. Two of my early claims were wrong ("a put is atomic" without qualification, and blob
     rows left orphaned), which tests and review caught; the claims that matter are now tested or marked as readings.

4. What failed or surprised me:
   - The default durability is `async`, not `sync`.
   - Under `sync`, the first tick runs before the input checkpoint is known to be durable. In a `StateGraph` that tick
     is the internal start step, but in a functional-API entrypoint the user's code ran while the input checkpoint's
     put was still in flight (one in-process experiment). This is reading R1 in the audit, a hypothesis to measure.
   - In the pipeline modes, a client-side exception between a put's two statements could commit the blob rows alone,
     and on Postgres a put that rolls back entirely can leave the next checkpoint without some channels.
   - `sync` waits for checkpoints, not for a step's task writes.

5. How the evidence supports the conclusion:
   - Commit points and windows: research/source-audit.md, sections 1, 2, 7, 8, and 10 (AW1 to AW6).
   - Tested claims: tests/audit/test_postgres_saver.py (10 cases against Postgres) and tests/audit/test_sqlite_saver.py.
   - The statements one `put` and one `put_writes` send: research/evidence/t-03/statements.log.
   - Upstream identity and search: research/evidence/t-03/upstream-search.txt (all 13 audited files byte-identical at
     their tags and in the installed wheels).
   - The first-tick order: research/evidence/t-03/first_tick_order.txt.
   - Two reviews of the audit, and their fixes: tasks/T-03.md.

Decision: PASS. GO: the target is sufficiently evidenced and technically interesting to continue studying.

This is not a novelty claim, and it is not permission to report anything upstream.

Conditions:
- Reading R1 (the functional-API entrypoint under `sync`) stays private. It is a hypothesis to measure, with its
  experiment cell preserved in the audit. It is not called a bug or a violation unless T-10's frozen checker and a
  standalone reproduction support that, and any report upstream is a separate decision.
- The other readings in the audit (task writes not awaited under `sync`, missing parents and channels after a failed
  put) are measured in their cells before anything is said about them.
- Overlapping prior art (#8796, #8039, #8764, #8234, the xbstack repro) stays credited; replicating it is a control,
  never a finding.
