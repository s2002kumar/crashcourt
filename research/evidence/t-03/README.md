# T-03 evidence

- `statements.log`: the statements `PostgresSaver` sent for one `put` (with one blob channel) and one `put_writes`,
  read back from the Postgres log. Written by `capture_statements.py`, which turns on `log_statement=all` for its own
  connection only:

      cd research/evidence/t-03
      uv run python capture_statements.py > statements.log

  It shows the blob upsert, then the checkpoint upsert, then the writes insert, as prepared statements, with no
  BEGIN or COMMIT: each call's transaction is the implicit one a pipeline Sync ends.
- `upstream-search.txt`: written by `search_upstream.py`, which shallow-clones langchain-ai/langgraph at the four
  pinned tags and records that every audited source file is byte-identical to the installed wheel; the card's test
  search (every hit in the saver tests and the conformance suite, per-file counts and matching test names in the
  runtime tests); the conformance suite's test counts at the tag and in the 0.0.2 release; and the repository's
  branches (source-audit.md, sections "Scope and method" and 9).
- `first_tick_order.txt`: written by `first_tick_order.py`, an in-process experiment against `InMemorySaver` that
  records when each checkpoint put starts and ends and when user code runs, under `durability="sync"`, for a
  `StateGraph` and for a functional-API entrypoint (source-audit.md, section 8).
- `tests/audit/test_postgres_saver.py` (integration, compose Postgres) and `tests/audit/test_sqlite_saver.py`: the
  direct-saver tests that research/source-audit.md cites. Run them with
  `docker compose up -d --wait && uv run pytest -m integration tests/audit` and `uv run pytest tests/audit`.
