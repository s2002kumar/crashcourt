# Source audit of the pinned savers (T-03, gate G0) · 2026-09-27, revised 2026-10-08

Question for G0: where exactly do the pinned savers and the Pregel write path commit state, and which code points
leave committed-but-unacknowledged state behind? The network and database fault cells (a lost acknowledgment, a
connection reset, a database restart) may only target windows that exist in this code.

## Scope and method

| Package | Version | Tag | Commit |
| --- | --- | --- | --- |
| langgraph | 1.2.12 | `1.2.12` | [`49cce0c`](https://github.com/langchain-ai/langgraph/tree/49cce0ca852be4cfb567a1cbe0e511ff325a1682) (2026-09-21) |
| langgraph-checkpoint | 4.2.0 | `checkpoint==4.2.0` | [`f55e772`](https://github.com/langchain-ai/langgraph/tree/f55e77274dad40897ef7b2d52cc7c0b85b792247) (2026-08-07) |
| langgraph-checkpoint-postgres | 3.1.2 | `checkpointpostgres==3.1.2` | [`fde3068`](https://github.com/langchain-ai/langgraph/tree/fde3068970679184b68d3d068a92c83c966a4888) (2026-08-07) |
| langgraph-checkpoint-sqlite | 3.1.1 | `checkpointsqlite==3.1.1` | [`b2926a0`](https://github.com/langchain-ai/langgraph/tree/b2926a0ff9589c28c7e01fe7cdbb337b86d5a4b4) (2026-07-30) |

- **The audited code is the code that runs.** All 13 langgraph files cited below are byte-identical at their tags and
  in the wheels uv.lock installs (research/evidence/t-03/upstream-search.txt). The psycopg citations are to the
  installed psycopg 3.3.6 wheel.
- **Main had not moved.** At `main` ([`7daa3ab`](https://github.com/langchain-ai/langgraph/tree/7daa3ab49d678a5da75edb08baa87db4a2be52c3),
  2026-09-23), the Postgres saver, the Pregel package, and the conformance suite were identical to the tags. The
  SQLite saver and the checkpoint base differed only in lint comments and one typing cast. Three delta-channel fixes
  (#8544, #8556, #8557) have merged since (section 9); none is in the pinned releases.
- **Four kinds of evidence:**
  - reading the code (citations below);
  - direct-saver tests in `tests/audit/`, which call only public saver methods and use the database as the witness
    (rows, `xmin`, commit timestamps);
  - a statement log of one `put` and one `put_writes`: research/evidence/t-03/statements.log, captured with
    per-connection `log_statement=all`;
  - one in-process ordering experiment for section 8 (research/evidence/t-03/first_tick_order.txt).
- **What is read from code and what is tested** is marked where it matters; a code reading is a claim to measure,
  not a finding.

Citation prefixes (each is a permalink base at the commit above):

| Prefix | Path |
| --- | --- |
| `pg/` | [`libs/checkpoint-postgres/langgraph/checkpoint/postgres/`](https://github.com/langchain-ai/langgraph/tree/fde3068970679184b68d3d068a92c83c966a4888/libs/checkpoint-postgres/langgraph/checkpoint/postgres) at `fde3068` |
| `sq/` | [`libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/`](https://github.com/langchain-ai/langgraph/tree/b2926a0ff9589c28c7e01fe7cdbb337b86d5a4b4/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite) at `b2926a0` |
| `cb/` | [`libs/checkpoint/langgraph/checkpoint/base/`](https://github.com/langchain-ai/langgraph/tree/f55e77274dad40897ef7b2d52cc7c0b85b792247/libs/checkpoint/langgraph/checkpoint/base) at `f55e772` |
| `pregel/` | [`libs/langgraph/langgraph/pregel/`](https://github.com/langchain-ai/langgraph/tree/49cce0ca852be4cfb567a1cbe0e511ff325a1682/libs/langgraph/langgraph/pregel) at `49cce0c` |

## 1. Postgres `put` / `aput`

**Statements, in order.** `put` splits channel values: `None`, `str`, `int`, `float`, and `bool` stay inline in the
checkpoint JSONB; everything else, and every `_DeltaSnapshot`, goes to `checkpoint_blobs` (pg/__init__.py:309-319;
pg/aio.py:268-278). It then sends:

1. `executemany` of `INSERT INTO checkpoint_blobs ... ON CONFLICT (thread_id, checkpoint_ns, channel, version) DO
   NOTHING`, one row per changed blob channel (pg/base.py:131-135; pg/__init__.py:322-333);
2. `INSERT INTO checkpoints ... ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE SET checkpoint,
   metadata` with `parent_checkpoint_id` taken from the caller's config (pg/base.py:137-144; pg/__init__.py:334-344).

No statement reads the thread first. **Nothing checks that the parent is still the head**, so any number of writers
can attach children to one parent. This fork is not new: langgraph#8796 and the xbstack two-process repro
(https://github.com/xbstack/langgraph-same-thread-concurrent-writer-repro) show it end to end, and
`test_two_sync_savers_fork_one_parent` and `test_two_async_savers_fork_one_parent` replicate it at the saver level.
The statement log shows exactly the two statements above for a put, with no BEGIN or COMMIT.

**Transaction boundary: one transaction per call.** `put` runs inside `_cursor(pipeline=True)`
(pg/__init__.py:404-442; pg/aio.py:363-403):

| Mode | Code | Boundary | Evidence |
| --- | --- | --- | --- |
| Default (`from_conn_string`, autocommit, no saver pipeline) and libpq supports pipelines | `with conn.pipeline(), conn.cursor()` (pg/__init__.py:427-432) | One implicit transaction, committed at the pipeline's Sync when the block exits. psycopg documents that "even in autocommit, the server wraps the statements sent in pipeline mode in an implicit transaction, which will be only committed when the Sync is received" ([psycopg pipeline docs](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html)). `executemany` rides an open pipeline "in order to avoid sending unnecessary Sync" (psycopg 3.3.6 `psycopg/cursor.py:128-134`) | Tested, sync and async |
| Saver built with `pipeline=True` | cursor on the long-lived pipeline, then `self.pipe.sync()` in `finally` (pg/__init__.py:414-423) | One implicit transaction per call, ended by that Sync | Tested |
| libpq without pipeline support | `with conn.transaction(), conn.cursor()` (pg/__init__.py:433-439) | An explicit transaction | Read from code |
| `ConnectionPool` | the pool lends one connection per `_cursor` (pg/_internal.py:17-19), then one of the rows above | Same as the row above; pools must be autocommit per the saver docs | Read from code |

What the tests show, in the three tested modes:

- `test_put_writes_blobs_and_checkpoint_in_one_transaction`: after a put with two blob channels, the checkpoint row
  and both blob rows carry the same `xmin`.
- `test_rejected_checkpoint_row_rolls_back_its_blobs`: Postgres rejects a NUL character in JSONB (SQLSTATE 22P05,
  which the test requires), so the checkpoint upsert fails after the blob upsert was sent, and no blob row survives.

What they do not show: that a put is atomic when the client side fails between its two statements. In the pipeline
modes, psycopg's pipeline exit sends a Sync on every exit, including when the block raised (psycopg 3.3.6
`psycopg/_pipeline.py:65-81`, `psycopg/_pipeline_async.py:61-77`, `psycopg/_pipeline_base.py:98-106`), and a saver
built with `pipeline=True` syncs in `finally` (pg/__init__.py:421-423). So an exception raised inside `put` after the
blob statement and before the checkpoint statement could commit the blob rows without their checkpoint. On the async
path, a cancellation can instead make psycopg ask the server to cancel the running statement
(`psycopg/connection_async.py:535-553`), which rolls the batch back. The no-pipeline fallback runs in an explicit
transaction and rolls back on any exception. All of this is read from code, not tested.

Blobs committed without their checkpoint are not left unreferenced: the run's next put writes only the channels that
changed since the previous put was submitted (section 7), so its checkpoint names those blob versions.

**Same id twice.** Re-putting an existing `(thread_id, checkpoint_ns, checkpoint_id)` overwrites that row's checkpoint
and metadata but keeps its original `parent_checkpoint_id`: the `DO UPDATE` sets only `checkpoint` and `metadata`
(pg/base.py:141-143).

## 2. `put_writes` / `aput_writes`

- **Statement choice.** If every write in the batch is to a special channel, the saver uses `INSERT ... ON CONFLICT
  (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO UPDATE` (pg/base.py:146-153). Otherwise it uses `... DO
  NOTHING` (pg/base.py:155-159). The choice is made per batch, not per row (pg/__init__.py:363-367; pg/aio.py:323-327).
- **Special channels.** `WRITES_IDX_MAP = {ERROR: -1, SCHEDULED: -2, INTERRUPT: -3, RESUME: -4}` (cb/__init__.py:795)
  gives these channels fixed negative indices; other writes use their position in the batch (pg/base.py:606).
- **Consequences, tested in `test_put_writes_conflict_rules`,** which reads the rows from `checkpoint_writes` directly.
  For the same task and index:
  - regular writes keep the **first** writer's value;
  - an all-special batch (such as a resume value) keeps the **last** writer's value;
  - a batch that mixes a regular write with a special one is insert-only throughout, so its special write also keeps
    the **first** value.
  - SQLite behaves the same, mixed batches included (`test_sqlite_put_writes_conflict_rules`).
- **Boundary.** One `executemany` in `_cursor(pipeline=True)`, so one transaction per call, as for `put`. Read from
  code: no test checks `put_writes`' transaction.

## 3. `get_tuple`, `list`

- **Latest.** Without a `checkpoint_id`: `WHERE thread_id AND checkpoint_ns ORDER BY checkpoint_id DESC LIMIT 1`
  (pg/__init__.py:233-235; pg/aio.py:201-203; sq/__init__.py:240).
- **By id.** With a `checkpoint_id`: an equality lookup on the primary key (pg/__init__.py:230-232).
- **What the read joins.** `SELECT_SQL` assembles channel values by joining `checkpoint_blobs` on the checkpoint's own
  `channel_versions`, and pending writes by `checkpoint_id` (pg/base.py:93-118).
- **Legacy checkpoints.** Version-below-4 checkpoints with a parent trigger a second query that migrates pending sends
  (pg/__init__.py:246-259). The runtime writes version 4 (pregel/_checkpoint.py:23). `langgraph.checkpoint.base.empty_checkpoint()`
  still produces version 2 (cb/__init__.py:811), so the audit tests build version-4 checkpoints directly.
- **`list`.** Orders by `checkpoint_id DESC` (pg/__init__.py:153). `before` becomes `checkpoint_id < %s`
  (pg/base.py:659-661).
- **"Latest" is decided by id, not by commit order.** `test_latest_is_the_largest_id_not_the_last_commit`: the
  checkpoint minted second but committed first is returned as latest, and `pg_xact_commit_timestamp(xmin)` shows the
  other one committed later.

## 4. Checkpoint ids and channel versions

- **Checkpoint ids are UUIDv6, not ULIDs** as the checkpointers docs page says.
  - The runtime mints them with `uuid6(clock_seq=step)` (pregel/_checkpoint.py:209).
  - The timestamp is `time.time_ns()`, the process's wall clock (cb/id.py:92). The node is 48 random bits
    (cb/id.py:101-102).
  - A module-global guard keeps ids increasing within one process (cb/id.py:96-98). Nothing orders ids across
    processes except their wall clocks, so with two workers, "latest" (section 3) is whichever worker's clock read
    later. Clock skew between workers reorders it directly.
- **Channel versions** are `"{n:032}.{random:016}"` (pg/base.py:613-622). Two writers advancing one channel from the
  same version produce the same integer part with different random suffixes. Their blob rows do not collide
  (`DO NOTHING` on `(thread, ns, channel, version)`), so each forked checkpoint keeps its own blobs.

## 5. Locks

| Saver | Lock | Scope |
| --- | --- | --- |
| `PostgresSaver` | `threading.Lock` (pg/__init__.py:59), held for every cursor (pg/__init__.py:413) | One saver instance in one process |
| `AsyncPostgresSaver` | `asyncio.Lock` (pg/aio.py:59, 374) | Same |
| `SqliteSaver` | `threading.Lock` (sq/__init__.py:95, 181) | Same |
| `AsyncSqliteSaver` | `asyncio.Lock` (sq/aio.py:127) | Same |

- **No pinned saver takes any inter-process lock**: no advisory lock, no `SELECT ... FOR UPDATE`, no lease. The
  instance lock #7259 describes for the async Postgres saver is an `asyncio.Lock`; neither saver's lock spans
  processes.
- **#8796's reference is to an unmerged test.** #8796 cites `test_put_waits_for_interprocess_write_lock` from PR #6985.
  That PR is still open and unmerged, and it targets the SQLite saver's tests; the test exists at no pinned tag and not
  on `main`.
- **Open PR #7269** would stop pooled async savers from sharing the instance lock, which is #7259's complaint.

## 6. SqliteSaver

- **Connection.** One connection opened with `check_same_thread=False` (sq/__init__.py:120-127). `setup` enables WAL
  (sq/__init__.py:139-164).
- **Busy timeout.** None is set explicitly, so Python's `sqlite3.connect` default of 5 seconds applies.
- **Commits.** Every write commits when its cursor context exits (sq/__init__.py:168-189). The async saver commits
  explicitly (sq/aio.py:552, 600).
- **Rows.** A checkpoint is one row with the whole state serialized in it: no blobs table, `INSERT OR REPLACE`
  (sq/__init__.py:424-436). Writes use `INSERT OR REPLACE` for an all-special batch and `INSERT OR IGNORE` otherwise
  (sq/__init__.py:462-466), the same first- and last-writer rules as Postgres, tested for mixed batches too.
- **Two savers on one file fork the thread** exactly as on Postgres (`test_two_sqlite_savers_fork_one_parent`, the
  #8796 fork replicated on SQLite). WAL serializes the writes; nothing checks the parent.

## 7. The Pregel write path

- **Task writes.**
  - When a task finishes, the runner's done-callback calls `loop.put_writes` (pregel/_runner.py:116-132, 574-613).
    `put_writes` records the writes in memory, then *submits* `checkpointer.put_writes` to the background executor
    (pregel/_loop.py:415-508; the submit is at :466-494).
  - The only background writes waited on are those to delta channels, before the next checkpoint put
    (pregel/_loop.py:495-498, 1538-1540), and error-handler writes (:502-505), before the error handler runs
    (:1579-1581).
- **The #8115 race is present in the code.**
  - `put_writes` replaces `self.checkpoint_pending_writes` with a filtered copy and then extends it
    (pregel/_loop.py:434-437, 457), with no lock.
  - With the sync driver, task futures complete on pool threads, and `FuturesDict.on_done` invokes the commit callback
    outside its own lock (pregel/_runner.py:122-124), so two tasks finishing together interleave there.
  - With the async driver, callbacks run on the event loop thread.
  - This matches #8115 (open). Exercising it end to end needs a parallel fan-out workload on the sync driver.
- **Exceptions from savers can be dropped.**
  - Background futures are awaited when the executor exits, but their first exception is re-raised only
    `if exc_type is None` (pregel/_executor.py:111 sync, :202 async).
  - When another exception is already propagating, a failed `put` or `put_writes` is swallowed. #8796 measured this.
- **A failed put does not stop the next one.**
  - `_checkpointer_put_after_previous` waits for the previous put inside `try` and calls `put` in `finally`
    (pregel/_loop.py:1541-1547; async :1796-1802).
  - If put N does not commit and put N+1 does, row N+1 names a parent that was never written. A comment on #8234
    reports reproducing exactly that
    ([#8234 comment](https://github.com/langchain-ai/langgraph/issues/8234#issuecomment-5547306337)), and open PR
    #9235 (2026-10-07) proposes not saving a checkpoint past a failed delta-channel write.
  - On Postgres, row N+1 can also lack channel values. Each put writes blobs only for the channels whose versions
    changed since the previous put was submitted (pregel/_loop.py:1193-1197; pregel/_utils.py:27-42;
    pg/__init__.py:322-324), and a read joins blobs on the checkpoint's own versions (pg/base.py:101-109). If put N
    rolled back entirely, the blobs it would have written are missing, and those channels silently drop out of the
    state `get_tuple` returns for N+1. SQLite stores the whole state in each row, so this is Postgres only. A reading
    to measure.
- **Every run starts from an unlocked read.** `__enter__` reads the head with `get_tuple` when no `checkpoint_id` is
  given (pregel/_loop.py:1651-1654) and never re-checks it. This check-then-act gap is what lets two workers resume one
  thread from the same head.

## 8. Durability modes

- **The default is `"async"`** (pregel/main.py:2602-2603).
- **Every checkpoint put is submitted in the background, never inline**: "save it, without blocking"
  (pregel/_loop.py:1199-1209).

| Mode | Where it branches | Waits for |
| --- | --- | --- |
| `sync` | after each tick, `loop._put_checkpoint_fut.result()` (pregel/main.py:2986-2988; async :3461-3462) | That tick's checkpoint put, and through the chain every earlier put. **Not** the tick's regular task writes |
| `async` | no wait | Nothing until the executor exits |
| `exit` | `do_checkpoint` is false except when exiting (pregel/_loop.py:1133-1135); `put_writes` skips the saver (:466) | One checkpoint and its pending writes saved in `_suppress_interrupt` at exit or interrupt (:1324-1334) |

**Under `sync`, the wait comes after a tick, so the first tick runs before the input checkpoint is known to be
durable.** `__enter__` calls `_first()` (pregel/_loop.py:1703), which submits the input checkpoint's put (:1033)
without waiting, and the first wait is after the first tick and `after_tick()` (pregel/main.py:2984-2988).

- In a `StateGraph` given an ordinary input, the first tick is the internal `__start__` step, and user nodes run from
  the next tick, after the input and step-0 puts committed. Comments on #8764 and #8039 traced this order on 1.2.11
  ([#8764 comment](https://github.com/langchain-ai/langgraph/issues/8764#issuecomment-5537080433),
  [#8039 comment](https://github.com/langchain-ai/langgraph/issues/8039#issuecomment-5590977016)), and
  research/evidence/t-03/first_tick_order.txt shows it on 1.2.12. A `Command` input on an existing thread resumes
  and writes no input checkpoint (pregel/_loop.py:858-873).
- In a functional-API `@entrypoint`, the first tick runs user code. In the same experiment, the entrypoint's body ran
  while the input checkpoint's put was still in flight.
- Because each put runs in `finally` after its predecessor (section 7), a failed put followed by a successful one
  leaves a row whose parent was never written (AW4). Under `async` that can happen at any step. Under `sync`, the wait
  after each tick limits it to the puts submitted before the first wait: the input checkpoint, or a time-travel
  run's fork checkpoint (pregel/_loop.py:971), and the first tick's put.

These are readings, backed by one in-process experiment against `InMemorySaver`; they are to be measured with the
pinned savers, not findings.

**Reading R1, kept for its own experiment cell.** A functional-API `@entrypoint` run with `durability="sync"` executes
user code before its input checkpoint is known to be durable. The cell to measure it: an entrypoint whose first
statement records an effect in the ledger, with a failpoint that kills the worker after that effect and before the
input checkpoint's put commits; afterwards, compare the ledger with the stored checkpoints and see what a resume does.
Until T-10's frozen checker and a standalone reproduction say otherwise, R1 is a hypothesis, not a bug or a
violation, and it is not reported upstream.

A finished node's writes are durable only when their background `put_writes` commits, and nothing waits for that
before the next tick. `invoke` returns only after the executor exits, which waits for every background write
(pregel/_executor.py:106-107), but streamed chunks arrive before that. #8039 reports this ordering under `sync`:
nothing orders a step's `put_writes` before the next `put`. The checkpointers page says the successful nodes' writes
"are already durable"; a crash after a node returns but before its writes commit loses them, and the node runs again
on resume, which the docs' re-execution guidance allows. This too is a reading of the code, to be measured, not a
finding.

## 9. Existing tests, branches, and open PRs

The searches below are reproduced by research/evidence/t-03/search_upstream.py; its output is
research/evidence/t-03/upstream-search.txt (run 2026-10-08): every hit in the saver tests and the conformance suite,
and per-file counts with the matching test names for the runtime tests.

- **Saver tests.** `libs/checkpoint-postgres/tests` at 3.1.2 and `libs/checkpoint-sqlite/tests` at 3.1.1 contain no
  test using a second saver, multiple processes, a barrier, an advisory lock, or `FOR UPDATE`. The card's pattern
  (`concurrent|race|fork|lock|advisory|interprocess`) hits seven lines there, all incidental: a `concurrent.futures`
  import in a store test, `clock_seq`, and "blocked" in assertion messages.
- **Runtime tests.** In `libs/langgraph/tests`, the tests whose names match are explicit forks (time travel,
  `update_state`, copied threads) and tracing. The ones named for concurrency, such as `test_concurrent_execution` and
  `test_concurrent_execution_thread_safety`, run ten invocations of a graph compiled without a checkpointer;
  `test_graph_error_handler_does_not_swallow_interrupt_concurrent` runs nodes concurrently within one superstep on one
  `InMemorySaver`. None uses two savers or two processes.
- **Conformance suite.** The published `langgraph-checkpoint-conformance` 0.0.2 has 81 capability tests in 8 files.
  At the 1.2.12 tag it has 89 in 9; the ninth file is delta channel history, with 8 tests. None involves a second
  saver instance. #8796 reports 89 tests in 9 files at `main`, which matches.
- **Branches.** 334 branches on 2026-10-08 (331 on 2026-09-27); none is named for locks, fencing, leases, ownership,
  or multiple writers. The name matches are incidental substrings ("release" contains "lease"; "uv-lock",
  "lockfile"), except `fix/delta-failed-write-blocks-checkpoint`, which is #9235's branch (below).
- **Open PRs touching savers or the conformance suite**, the ones relevant here, as of 2026-10-08:

| PR | What | State | Relevance |
| --- | --- | --- | --- |
| #8838 (draft, by a maintainer) | `queue_state`: a durable queue of state updates applied at superstep boundaries, "exactly once per run" | Open | A new durable write path with a written guarantee; steering, not per-thread run serialization. Watch item |
| #9235 | Don't save a checkpoint past a failed delta-channel write | Open (2026-10-07) | The failed-put-then-successful-put window (section 7, AW4) for delta channels |
| #6985 | SQLite inter-process write-lock test | Open | The test #8796 cites; unmerged |
| #7269 | No shared async lock for pooled async savers | Open | #7259 |
| #7237 | Restart-safety coverage for `put_writes` idempotency | Open | Adjacent to reusing committed task writes on resume |
| #7207 | Ignore late writes after `delete_thread` | Not found on 2026-10-08 (listed on 2026-09-27) | The silent-refusal shape #8796 argues against |
| #6871 | Deterministic ordering for same-timestamp checkpoints | Open | Tie-break for "latest" (section 4) |
| #6883 | Extended methods and conformance tests for the Postgres saver | Open | Conformance coverage of the primary target |
| #6759 | Race in `FuturesDict` | Open | The runner code in section 7 |
| #8242 | Delta replay for a channel migrated from a non-delta channel | Open | Delta channel reconstruction |
| #8544, #8556, #8557 | Delta-channel replay order and ancestor walks | Merged since 2026-09-27 | Delta channel reconstruction; not in the pinned releases |

## 10. Ambiguous-commit windows (input to the network and database fault cells)

Each window below commits on the server before the client learns the outcome. A lost acknowledgment or a connection
reset between the server's commit and the client's receipt of the result leaves committed-but-unacknowledged state.
Toxiproxy turns each one into a deterministic schedule on the server-to-client stream: a `timeout` toxic with
`timeout=0` drops the response, which is the lost acknowledgment, and a `latency` toxic holds it, which is a delayed
one. T-05's self-test checks both directions.

| ID | Window | Commit point | What the client sees | What to measure |
| --- | --- | --- | --- | --- |
| AW1 | Checkpoint put | Pipeline Sync at the end of `put`'s `_cursor(pipeline=True)` (pg/__init__.py:321-344; pg/aio.py:280-304) | A connection error from the background future. Under `sync`, raised at the next wait (pregel/main.py:2988); under `async`, deferred to executor exit | Does a retry fork from, duplicate, or regress past the committed checkpoint? Is every checkpoint acknowledged under `sync` present afterwards? |
| AW2 | Task writes | Sync at the end of `put_writes` (pg/__init__.py:368-379) | Only at executor exit, and dropped if another exception is in flight (pregel/_executor.py:111) | Are committed writes reused on resume while the client saw a failure? |
| AW3 | Resume value | All-special batch, `DO UPDATE` (pg/base.py:146-153) | As AW2 | A retried or conflicting resume replaces a value that may already have been acted on |
| AW4 | Failed put followed by a successful put | `finally` in `_checkpointer_put_after_previous` (pregel/_loop.py:1541-1547) | The first error, raised later | A checkpoint whose parent row is missing (any step under `async`, the first ones under `sync`; section 8); on Postgres, channels missing from that checkpoint when the failed put rolled back (section 7) |
| AW5 | First checkpoint of a run | The input checkpoint's put (pregel/_loop.py:1033) | As AW1 | #8764's loss before the first checkpoint, now under a lost acknowledgment rather than a crash |
| AW6 | Database restart | A put in flight when the server stops | A connection error; the commit either happened or not | After restart, is every checkpoint acknowledged under `sync` present? |

## G0

Gate question: are the transaction boundaries and ambiguous-commit windows located in code at the pinned tags?

- Every write path commits in one implicit or explicit transaction per call. Tested against a real Postgres for `put`
  in three connection modes; read from code for `put_writes`, the pool path, and the no-pipeline fallback.
- A `put` rolls back when the server rejects its checkpoint row; in the pipeline modes, a client-side exception
  between its two statements could commit the blob rows alone (section 1).
- Six ambiguous-commit windows are located with citations (section 10).
- The storage-level fork is replicated at the saver level (section 1), consistent with #8796 and the xbstack repro.
- Nothing found removes the network-fault cells. AW1 to AW3 are the targets for the lost-acknowledgment and
  connection-reset faults.

**G0: GO** (research/gates/g0-note.md, 2026-10-09). Not a novelty claim. Until measured, reading R1 and the other
unmeasured readings may be public only as explicitly labeled readings or hypotheses, never as results, findings, or
violations (the G0 note's condition, amended 2026-10-16).
