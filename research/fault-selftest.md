# Fault self-test (T-05) · 2026-10-08

`crashcourt faults selftest` checks every fault primitive against the compose services:

- Toxiproxy 2.12.0 and Postgres 18.6, pinned by digest.
- Each network check runs one INSERT through the proxy (`127.0.0.1:15432`) while a direct connection (`127.0.0.1:55432`,
  the oracle path) watches whether it committed.
- A check passes only when the client's view and the database both match the semantics below. A primitive that raises
  fails its check. The headline names any check that was skipped or not run.

Run it with `docker compose up -d --wait && uv run crashcourt faults selftest --trials 20`.

## Result on Linux CI

From CI run [37875936339](https://github.com/s2002kumar/crashcourt/actions/runs/37875936339) (ubuntu-24.04), artifact
`doctor-evidence/fault-selftest-ci.md`, verbatim:

Trials passed: 20 of 20 (a trial passes when no check fails). Skipped: none. Not run: none.

| Check | Pass | Fail | Skip | First trial's observation |
| --- | --- | --- | --- | --- |
| downstream_drop_is_lost_ack | 20 | 0 | 0 | committed_while_client_waited=True, client_blocked_until_heal=True, client_after_heal=OperationalError: consuming input failed: server closed the connection unexpectedly, committed_final=True |
| upstream_drop_never_reaches_server | 20 | 0 | 0 | committed_while_client_waited=False, client_blocked_until_heal=True, client_after_heal=OperationalError: consuming input failed: server closed the connection unexpectedly, committed_final=False |
| downstream_hold_is_delayed_ack | 20 | 0 | 0 | committed_while_client_waited=True, client_blocked_until_heal=True, client_after_heal=ok, committed_final=True |
| downstream_latency_delays_response | 20 | 0 | 0 | latency_ms=200, client=ok, elapsed_ms=201.0, committed=True |
| reset_downstream_after_commit | 20 | 0 | 0 | client=OperationalError: consuming input failed: server closed the connection unexpectedly, committed=True |
| reset_upstream_before_execute | 20 | 0 | 0 | client=OperationalError: consuming input failed: server closed the connection unexpectedly, committed=False |
| new_toxic_on_held_stream_releases_hold | 20 | 0 | 0 | api_refused_second_toxic=True, upstream_add_released_hold=False, downstream_add_released_hold=True |
| terminate_backend | 20 | 0 | 0 | terminated=1, client_next_query=AdminShutdown [57P01]: terminating connection due to administrator command |
| restart_database | 20 | 0 | 0 | acked_row_survived=True, open_connection=AdminShutdown [57P01]: terminating connection due to administrator command, container=e4053d569a91, restart_s=0.272, ready_s=0.108 |
| signals | 20 | 0 | 0 | advanced_while_stopped=0, resumed=True, sigterm_returncode=-15, sigkill_returncode=-9 |
| inject_heal_pairs | 20 | 0 | 0 | injects=11, heals=11, failed_heals=0 |

## Semantics established by the test

| Primitive | Stream | Server executes and commits? | Client sees | Use |
| --- | --- | --- | --- | --- |
| `timeout` toxic, timeout=0, then removed | downstream | Yes, while the client waits | Connection closed when the toxic is removed | Lost acknowledgment |
| `timeout` toxic, timeout=0, then removed | upstream | No, the request is dropped | Connection closed when the toxic is removed | Request lost before execution |
| `latency` toxic, 24 h, then removed | downstream | Yes, while the client waits | Success, as soon as the toxic is removed | Delayed acknowledgment (deterministic) |
| `latency` toxic, 200 ms | downstream | Yes | Success, at least 200 ms later | Slow network |
| `reset_peer`, timeout=0 | downstream | Yes; the reset hits the first response | Connection closed | Lost acknowledgment in one step |
| `reset_peer`, timeout=0 | upstream | No | Connection closed | Connection reset before execution |
| `pg_terminate_backend` on every matching backend in one scan, then a wait until they are gone | none (direct) | Nothing in flight | `AdminShutdown` (57P01) on the next statement | Backend termination |
| `docker compose restart postgres` | none (container) | Acknowledged commits survive | Open connections end with 57P01 (fast shutdown) | Fast database restart |
| SIGSTOP, then SIGCONT | none (process) | n/a | The process stops making progress, then resumes | Process pause |
| SIGTERM; SIGKILL | none (process) | n/a | Exit status -15; -9 | Graceful stop; kill |

`downstream` is server to client and `upstream` is client to server, as the Toxiproxy README states; the direction
checks confirm it from what committed.

Every fault writes `fault.inject` before it is applied and one `fault.heal` when it ends. The heal carries what the
fault observed (an exit status, how many backends ended, how long a restart took), and a failed inject or heal has
status `fail` with the error in the envelope.

## The hold the plan assumed

The plan was to hold responses with a `timeout` toxic set to timeout=0 on the downstream, and then choose: release them
for a delayed acknowledgment, reset the connection for a lost one, or kill the worker. That toxic holds nothing. It
discards what it reads.

- **Source.** In Toxiproxy 2.12.0 the `timeout` toxic reads each chunk and discards it, and closes the connection when
  it is removed ([timeout.go](https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/timeout.go)). The README describes the toxic the same way
  ([README](https://github.com/Shopify/toxiproxy/blob/v2.12.0/README.md)).
- **Measured.** The INSERT commits, the client receives nothing while the toxic is in place, and removing the toxic
  closes the client's connection. The response never arrives, so this toxic is a lost acknowledgment and cannot be a
  delayed one.
- **What the harness uses instead.**
  - Delayed acknowledgment: a downstream `latency` toxic of 24 hours. Removing it forwards what it was holding
    ([latency.go](https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/latency.go)).
  - Lost acknowledgment: the `timeout` drop, or `reset_peer` on the downstream
    ([reset_peer.go](https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/reset_peer.go)).
- **What changes.** A schedule decides between a delayed and a lost acknowledgment when it adds the toxic, not when it
  heals it, because removing a toxic does whatever that toxic does. One held response cannot later be either released
  or dropped.
- **A trap.** A new toxic on the held stream interrupts the latency toxic and releases the response, in the check above
  at the moment the second toxic is added; a toxic on the other stream does not. The fault functions refuse to add any
  toxic while a hold is active.

## Other observations

- **Toxics apply per proxy, so to every connection through it.** With one proxy, any network fault in a two-worker
  schedule would hit both workers. T-07 (#17) gives each worker its own proxy and port.
- **Restart as seen from Windows.** Through Docker Desktop's networking, an open connection saw a socket abort instead
  of 57P01 during the restart; on Linux, fast shutdown terminates backends cleanly. Official runs are on Linux.
- **Signals.** Windows has no SIGSTOP or SIGCONT, so on a Windows host the signal check reports `skip`, and the headline
  says so. CI runs it on Linux.
- **Local run on the Windows development host (2026-10-08).** 3 of 3 trials, every check passing except the signal
  check, skipped.

## Sources
Retrieved 2026-09-27:
- https://github.com/Shopify/toxiproxy/blob/v2.12.0/README.md (toxics, stream directions, HTTP API)
- https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/timeout.go, https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/latency.go, https://github.com/Shopify/toxiproxy/blob/v2.12.0/toxics/reset_peer.go

Retrieved 2026-10-08:
- https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-ADMIN-SIGNAL (`pg_terminate_backend` returns true
  once the signal is sent, whether or not the backend exits; with a timeout it waits for the exit)
