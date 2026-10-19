"""Mitigations under test in the N8 mitigation study: a storage-checked ownership fence
(crashcourt.mitigation.fence) and an idempotent effect sink (crashcourt.sink.ledger). Each is
applied by the harness around the unchanged saver and judged by a contract of its own
(crashcourt.mitigation.contract), beside the frozen checker's reading, never instead of it."""
