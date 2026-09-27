# T-03 evidence

- `statements.log`: the statements `PostgresSaver` sent for one `put` (with one blob channel) and one `put_writes`,
  read back from the Postgres log. Written by `capture_statements.py`, which turns on `log_statement=all` for its own
  connection only:

      uv run python research/evidence/t-03/capture_statements.py > research/evidence/t-03/statements.log

  It shows the blob upsert, then the checkpoint upsert, then the writes insert, as prepared statements, with no
  BEGIN or COMMIT: each call's transaction is the implicit one a pipeline Sync ends.
- `tests/audit/test_postgres_saver.py` (integration, compose Postgres) and `tests/audit/test_sqlite_saver.py`: the
  direct-saver tests that research/source-audit.md cites. Run them with
  `docker compose up -d --wait && uv run pytest -m integration tests/audit` and `uv run pytest tests/audit`.
