# Fault self-test (T-05) · 2026-09-27

`crashcourt faults selftest` checks every fault primitive against the compose services:

- Toxiproxy 2.12.0 and Postgres 18.6, pinned by digest.
- Each network check runs one INSERT through the proxy (`127.0.0.1:15432`) while a direct connection (`127.0.0.1:55432`,
  the oracle path) watches whether it committed.
- A check passes only when the client's view and the database both match the semantics below.

Run it with `docker compose up -d --wait && uv run crashcourt faults selftest --trials 20`.

## Result on Linux CI: 20 of 20

From CI run [36321089370](https://github.com/s2002kumar/crashcourt/actions/runs/36321089370) (ubuntu-24.04), artifact
`doctor-evidence/fault-selftest-ci.md`, verbatim:

| Check | Pass | Fail | Skip | First trial's observation |
| --- | --- | --- | --- | --- |
| downstream_drop_is_lost_ack | 20 | 0 | 0 | committed_while_client_waited=True, client_blocked_until_heal=True, client_after_heal=OperationalError: consuming input failed: server closed the connection unexpectedly, committed_final=True |
| upstream_drop_never_reaches_server | 20 | 0 | 0 | committed_while_client_waited=False, client_blocked_until_heal=True, client_after_heal=OperationalError: consuming input failed: server closed the connection unexpectedly, committed_final=False |
| downstream_hold_is_delayed_ack | 20 | 0 | 0 | committed_while_client_waited=True, client_blocked_until_heal=True, client_after_heal=ok, committed_final=True |
| reset_downstream_after_commit | 20 | 0 | 0 | client=OperationalError: consuming input failed: server closed the connection unexpectedly, committed=True |
| reset_upstream_before_execute | 20 | 0 | 0 | client=OperationalError: consuming input failed: server closed the connection unexpectedly, committed=False |
| new_toxic_on_held_stream_releases_hold | 20 | 0 | 0 | api_refused_second_toxic=True, upstream_add_released_hold=False, downstream_add_released_hold=True |
| terminate_backend | 20 | 0 | 0 | terminated=1, client_next_query=AdminShutdown [57P01]: terminating connection due to administrator command |
| restart_database | 20 | 0 | 0 | acked_row_survived=True, open_connection=AdminShutdown [57P01]: terminating connection due to administrator command, container=29b6388fd1b1, restart_s=0.348, ready_s=0.112 |
| signals | 20 | 0 | 0 | advanced_while_stopped=0, resumed=True, sigterm_returncode=-15, sigkill_returncode=-9 |
| inject_heal_pairs | 20 | 0 | 0 | injects=10, heals=10 |

## Semantics established by the test

| Primitive | Stream | Server executes and commits? | Client sees | Use |
| --- | --- | --- | --- | --- |
| `timeout` toxic, timeout=0, then removed | downstream | Yes, while the client waits | Connection closed when the toxic is removed | **Lost ack** (F11) |
| `timeout` toxic, timeout=0, then removed | upstream | No, the request is dropped | Connection closed when the toxic is removed | Request lost before execution |
| `latency` toxic, 24 h, then removed | downstream | Yes, while the client waits | Success, as soon as the toxic is removed | **Delayed ack** (F12, deterministic) |
| `reset_peer`, timeout=0 | downstream | Yes; the reset hits the first response | Connection closed | One-step lost ack (F10, F11) |
| `reset_peer`, timeout=0 | upstream | No | Connection closed | Reset before execution (F10) |
| `pg_terminate_backend` | none (direct) | Nothing in flight | `AdminShutdown` (57P01) on the next statement | F10, F14 |
| `docker compose restart postgres` | none (container) | Acknowledged commits survive | Open connections end with 57P01 (fast shutdown) | F13 |
| SIGSTOP, then SIGCONT | none (process) | n/a | The process stops making progress, then resumes | F03 |
| SIGTERM; SIGKILL | none (process) | n/a | Exit status -15; -9 | F15; F01 |

`downstream` is server to client and `upstream` is client to server, as the Toxiproxy README states; the direction
checks confirm it from what committed.

## Where the spec's assumption was wrong

Spec section 10 says: "A `timeout` toxic with `timeout=0` on the server-to-client stream holds responses until
removed", and that the orchestrator can then release it (delayed ack), reset it (lost ack), or kill the worker. The
first half holds and the second does not.

- **Source.** `timeout.go` at v2.12.0 reads each chunk and drops it ("Drop the data on the ground"), and its `Cleanup`
  closes the connection when the toxic is removed. The README says the same: "data will be dropped until the toxic is
  removed".
- **Measured.** Removing the toxic never delivers the response; the client gets a closed connection. The `timeout`
  toxic is therefore a lost acknowledgment, never a delayed one.
- **What the harness uses instead.**
  - Delayed ack: a very long downstream `latency` toxic. On removal, `latency.go` forwards the chunk it was holding
    ("Don't drop any data on the floor").
  - Lost ack: the `timeout` drop, or `reset_peer` on the downstream.
- **A trap this creates.** A new toxic on the held stream interrupts the latency toxic and releases the response, in
  the check above at the moment the second toxic is added; a toxic on the other stream does not. The fault functions
  refuse to add any toxic while a hold is active.

F11 and F12 keep the semantics section 10 assigns them; only the Toxiproxy primitive behind F12 changes.

## Other observations

- **Toxics apply per proxy, so to every connection through it.** A fault meant for one worker needs that worker to
  have its own proxy (and port). This is for T-07 and T-12 to settle; compose publishes one proxied port today.
- **Restart as seen from Windows.** Through Docker Desktop's networking, an open connection saw a socket abort instead
  of 57P01 during the restart; on Linux, fast shutdown terminates backends cleanly. Official runs are on Linux.
- **Signals.** Windows has no SIGSTOP or SIGCONT, so on a Windows host the signal check reports `skip`. Before CI, the
  signal check ran 20 times in a Linux container (`ghcr.io/astral-sh/uv:0.12.1-python3.12-trixie-slim`, kernel
  6.6.87.2-microsoft-standard-WSL2): 20 of 20.
- **Local run on the Windows development host.** Every other check passed in each trial run locally (3 of 3 before
  CI), including the database restart.

## Sources (retrieved 2026-09-27)
- https://github.com/Shopify/toxiproxy/blob/v2.12.0/README.md (toxics, stream directions, HTTP API)
- https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/timeout.go,
  https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/latency.go,
  https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/reset_peer.go
