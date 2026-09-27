# Failpoint wrapper overhead on `put` (T-06, control C4)

Host: Linux 6.6.87.2-microsoft-standard-WSL2, Python 3.12.13; Postgres 18.6 in Docker, reached directly at postgres:5432/crashcourt (no Toxiproxy).
The three variants run interleaved, one call each per round in a rotating order, after 50 warm-up rounds.
"With history" writes the `saver.put.call` and `.return` events to a JSONL file, as a worker does.

| Operation | Variant | Calls | p50 (µs) | p99 (µs) | Change vs raw, p50 / p99 |
| --- | --- | --- | --- | --- | --- |
| InMemorySaver.put | raw | 3000 | 5.6 | 21.6 |  |
| InMemorySaver.put | wrapped, no history | 3000 | 9.0 | 32.7 | +60.9% / +51.8% |
| InMemorySaver.put | wrapped, with history | 3000 | 45.4 | 188.6 | +712.2% / +774.6% |
| PostgresSaver.put | raw | 500 | 1846.4 | 6651.6 |  |
| PostgresSaver.put | wrapped, no history | 500 | 1834.1 | 6367.8 | -0.7% / -4.3% |
| PostgresSaver.put | wrapped, with history | 500 | 2081.7 | 6741.1 | +12.7% / +1.3% |
| AsyncPostgresSaver.aput | raw | 500 | 3303.0 | 6938.9 |  |
| AsyncPostgresSaver.aput | wrapped, no history | 500 | 3312.1 | 7141.3 | +0.3% / +2.9% |
| AsyncPostgresSaver.aput | wrapped, with history | 500 | 3671.3 | 8037.7 | +11.1% / +15.8% |
