# Conformance through the failpoint wrapper (T-06, control C4)

langgraph-checkpoint-conformance 0.0.2; savers at the uv.lock pins; host Windows 11, Python 3.12.13.
Each suite runs once on the raw saver and once on the same saver wrapped (disarmed).
Postgres gets a fresh database per capability suite. The comparison is the whole report (`CapabilityReport.to_dict()` without the name).

### InMemorySaver

Identical: **yes**. Conformance level: raw FULL, wrapped FULL.

| Capability | Raw: detected / passed / failed / skipped | Wrapped: detected / passed / failed / skipped |
| --- | --- | --- |
| put | True / 17 / 0 / 0 | True / 17 / 0 / 0 |
| put_writes | True / 10 / 0 / 0 | True / 10 / 0 / 0 |
| get_tuple | True / 10 / 0 / 0 | True / 10 / 0 / 0 |
| list | True / 16 / 0 / 0 | True / 16 / 0 / 0 |
| delete_thread | True / 5 / 0 / 0 | True / 5 / 0 / 0 |
| delete_for_runs | False / 0 / 0 / 1 | False / 0 / 0 / 1 |
| copy_thread | False / 0 / 0 / 1 | False / 0 / 0 / 1 |
| prune | False / 0 / 0 / 1 | False / 0 / 0 / 1 |

Failing tests on the raw saver: none.

### AsyncSqliteSaver

Identical: **yes**. Conformance level: raw FULL, wrapped FULL.

| Capability | Raw: detected / passed / failed / skipped | Wrapped: detected / passed / failed / skipped |
| --- | --- | --- |
| put | True / 17 / 0 / 0 | True / 17 / 0 / 0 |
| put_writes | True / 10 / 0 / 0 | True / 10 / 0 / 0 |
| get_tuple | True / 10 / 0 / 0 | True / 10 / 0 / 0 |
| list | True / 16 / 0 / 0 | True / 16 / 0 / 0 |
| delete_thread | True / 5 / 0 / 0 | True / 5 / 0 / 0 |
| delete_for_runs | False / 0 / 0 / 1 | False / 0 / 0 / 1 |
| copy_thread | False / 0 / 0 / 1 | False / 0 / 0 / 1 |
| prune | False / 0 / 0 / 1 | False / 0 / 0 / 1 |

Failing tests on the raw saver: none.

### AsyncPostgresSaver

Identical: **yes**. Conformance level: raw FULL, wrapped FULL.

| Capability | Raw: detected / passed / failed / skipped | Wrapped: detected / passed / failed / skipped |
| --- | --- | --- |
| put | True / 17 / 0 / 0 | True / 17 / 0 / 0 |
| put_writes | True / 10 / 0 / 0 | True / 10 / 0 / 0 |
| get_tuple | True / 10 / 0 / 0 | True / 10 / 0 / 0 |
| list | True / 16 / 0 / 0 | True / 16 / 0 / 0 |
| delete_thread | True / 5 / 0 / 0 | True / 5 / 0 / 0 |
| delete_for_runs | False / 0 / 0 / 1 | False / 0 / 0 / 1 |
| copy_thread | False / 0 / 0 / 1 | False / 0 / 0 / 1 |
| prune | False / 0 / 0 / 1 | False / 0 / 0 / 1 |

Failing tests on the raw saver: none.
