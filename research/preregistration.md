# Pre-registration: the first fault matrix

**Status: DRAFT, for the first discovery tranche.** This file declares a tranche (below, "The first
discovery tranche"): the cells it is being finalized for. Every other cell is deferred, with its reason,
and is never authorized. A draft authorizes only an instrument cell that is complete on its own, under
complete pins, and never new evidence (below, "Instrument cells before the final commit"). `crashcourt
experiment authorize` refuses every new-evidence batch until `state` is `final`, which needs everything
under "Before this is final" and my commit; `final` then means final for the tranche, and nothing about
the deferred cells.

This file fixes, before any batch of the first matrix runs, what each cell runs, how many trials it gets, and
what I predict it shows. The commit that last changes it is the pre-registration commit. A batch runs only
from a descendant of that commit, under the checker it names, and the summary shows every cell's prediction
beside what it observed. Deviations are logged below, never fixed silently.

## What is binding

The JSON block at the end is the binding part; the prose and the table explain it.

- **The checker.** `checker` names the frozen checker by two hashes: the checker files'
  (`checker_sha256`), and `src/crashcourt/checker/FROZEN.md`'s bytes (`frozen_md_sha256`), which also pin the
  clause registry, the workloads' effect models, and every scenario's landmark table. The commit that last
  changed FROZEN.md must be an ancestor of this file's commit. Pinned: checker v1, frozen by T-10 in
  2802584 (#28), with every hash its FROZEN.md records.
- **The builder.** `builder` pins `src/crashcourt/orchestrator/declared.py`, the code that turns a batch's
  own records into the checker's input (`trial_scenario`), by the sha256 of its bytes. It is not part of
  the frozen checker, so it is pinned here: a batch whose pre-registration or run commit holds any other
  version of it is refused, and verification refuses it again later. A change to it before this is final
  updates the pin in the same commit (a test fails otherwise).
- **Landmarks in the scenarios.** Every scenario step that arms a failpoint names its audited landmark
  (`landmark:`), which must be in the table the scenario names (`landmarks:`) at the step's method and
  occurrence; loading refuses a missing, unknown, or mismatched one, and the name is part of the
  scenario's hash.
- **The tranche.** `tranche` names the cells this file is finalized for; every other cell carries
  `deferred`, the reason it is outside the tranche. A deferred cell keeps its entry and its predictions,
  needs no scenario hash or seed for the file to be final, and is refused by the gate and by verification
  whatever the file's state. The instrument cells that gate new evidence are the tranche's. Widening the
  tranche changes this file, so it is a new pre-registration commit, under which every instrument cell
  runs again; batches of different commits never pool.
- **The cells.** Each cell names its scenario file and that scenario's content hash, its master seed, N, its
  phase, the number of worker processes its scenario declares (`workers`, a replacement that takes a
  redelivered op over included), and its predicted outcome per invariant. One prediction value is firm;
  several mean one of them, the same in every trial; `*` covers every invariant the cell does not name.
  `NOT_EXERCISED` predicts that the invariant is omitted from the results. Cells whose outcome is honestly
  unknown say `unknown`; their predictions record the expected shape and are compared, but not flagged.
- **Observations.** A cell may also predict what it observes apart from any outcome: rows of the effect
  ledger's export (by effect, with their count and attempts), a record in `checker.json` that is not an
  outcome, or whether an invariant showed an anomaly at all, whatever its class. Each is matched in every
  valid trial. Every replication names its published observation this way, so it is checked even where
  its outcome is GAP or nothing is scored: langgraph#8039 (K1a) by two charge rows with attempts 1 and 2,
  langgraph#8764 (K4) by the record of an accepted run with no durable record, and langgraph#8796 (K3) by
  the fork.
- **The environment.** Every batch's manifest must hold each value under `environment`.
- **Instrument checks.** Checks that are not batches each need recorded evidence before this is final:
  artifact and saver mutants (C5), checker determinism, transparency (C4), the sequential reference (C9), the
  clause registry (C6), environment equality (C7), the fault primitives' self-test, and the oracle's
  independence from the faults. Replay fidelity (C8) and the fixed-variant control (C10) apply to a candidate
  finding during escalation, not to a batch, so they are not listed here. C5's evidence is the path of its
  controls report, which the gate reads before any new evidence: its requirement must be the methodology's
  words exactly (`crashcourt.experiment.controls.C5_REQUIREMENT`), and the report must hold its exclusions
  beside its results (revised 2026-10-12, below).

## How it is enforced

- `crashcourt experiment authorize <scenario> --trials N` runs before doctor and refuses unless the checkout
  is clean; this file is committed and can authorize the cell (final, or a draft and the cell a complete
  instrument cell under complete pins); the checker is FROZEN and verifies against FROZEN.md, whose
  hash and checker hash are the ones named here; the TrialScenario builder is the pinned file at this
  file's commit and at HEAD; the scenario's hash, cell, master seed, and number of workers match its cell;
  N is the cell's N; the scenario's landmark table verifies against the freeze; and, for a new-evidence
  cell, every instrument cell has come out as predicted. It writes `authorization.json` into the batch.
- After doctor, the batch's manifest must hold the pre-registered environment, name the authorized commit,
  and record a clean checkout.
- `crashcourt experiment verify <batch>` re-proves an archived batch from git: this file's commit is an
  ancestor of the run commit and did not change before it, and could authorize the cell as it stood there;
  the freeze commit is an ancestor of this file's commit, every hash matches (the builder's among them),
  there are exactly N trials, and every trial's checker.json was written by the frozen checker and
  authorized for new evidence.
- `crashcourt experiment aggregate <batch>...` summarizes verified batches only, into `cells.json`. Batches
  are pooled only when they share this commit, the checker, and the scenario, and their manifests differ in
  no material field a recorded compatibility rule does not cover. Anything else is reported as separate
  groups, with the differing fields named.

## Run order

The instrument runs first. Every `instrument` cell of the tranche (C1 and C3 on both workloads, C2 as K2,
and the three published replications, langgraph#8039 as K1a, #8796 as K3, and #8764 as K4) must come out
as predicted, in its outcomes and its observations, under this commit, the frozen checker, and the same
environment before any `new-evidence` cell is authorized. A replication that disagrees with its published observation opens a harness investigation; it
is never a finding. The specification's C11 bar is 19 of 20; these cells are deterministic, so the gate asks
for every valid trial, and a split marks the cell UNSTABLE before it can count as a replication.

## Seeds

Each trial's seed is the sha256 of the JSON array `[master_seed, cell, trial]`. The master seed is written
in the scenario file, so the scenario hash pins it; nothing chooses a seed per cell or per trial. A rerun is
a new batch.

## Statistics and wording

- N is fixed per cell: 20 for every fault cell, and 50 per workload variant and durability for C1 and C3 in
  this discovery batch. A cell runs its full N even after its first anomaly, and every trial is archived.
- Every cell reports k of N with the Wilson 95% interval, and the rule-of-three bound when k is 0. The
  interval is shown even at 20 of 20.
- A deterministic cell is expected at 0 of N or N of N. Anything between marks it UNSTABLE: not reported until
  the hidden nondeterminism is named and removed, and the cell is rerun as a new batch.
- INVALID trials are counted and listed with their reasons. A cell above 2% INVALID is excluded and
  investigated.
- n counts only the trials where an invariant was exercised; an invariant that was not exercised is never a
  PASS.
- No significance tests, no "number of bugs found", and no sentence with "safe", "proven", "no bugs", or
  "never": the stats module's linter rejects them.
- Nothing is reclassified after the fact. A changed checker is a new version whose results sit beside the old.
- Any VIOLATION_CANDIDATE starts the escalation chain: a rerun as a new batch, the negative controls, a direct
  read of the database rows, the ledger chain, an alternate schedule, a standalone repro, a root-cause trace,
  a prior-art recheck, and an independent review. Only then can it become a VIOLATION, written by hand.

## Before this is final

It stays a draft until every one of these holds, for the first discovery tranche:

- Every tranche cell's scenario file exists, and its hash and master seed are copied into the cell. Done
  (2026-10-17): C1-w1-sync, C3-w1-sync, C1-w5-sync, C3-w5-sync, K2, K4, K1a, K3, and N8.
- T-10 has audited the real fault-free histories: the landmarks are derived from them, not from reading the
  source. Done for sync; the async occurrences (A1-A4) are deferred with their cells.
- T-10's FROZEN.md exists, and `checker` pins its hashes. Done: checker v1, freeze commit 2802584.
- Every `pending` entry of a tranche cell is resolved. Done: none is pending.
- Every checker record a cell observes (K4's) is worded as the frozen checker writes it. Done.
- The environment and runner fingerprint is fixed under `environment`. Done (2026-10-15).
- Every instrument check has its evidence recorded: C5 and C9 as controls reports the gate reads, produced
  on the canonical runner from committed sources; the rest as the tests, CI jobs, and recorded evidence that
  show them.
- Every tranche cell predicts every invariant the aggregator can score (the ruling of 2026-10-16).

Then `state` becomes `final` in my commit. The deferred cells stay deferred: their hashes, seeds, pending
entries, and evidence are a later tranche's, and this file's being final says nothing about them.

T-09's fault-free histories, the current set and the final one after the restack, are inputs to the instrument
and to the landmark derivation before the freeze. Neither is evidence about any cell here.

## Instrument cells before the final commit

Added 2026-10-15, before any batch was authorized. A draft can authorize an instrument cell once that cell
is complete (its scenario file exists, its hash and master seed are copied in, nothing is pending) and the
pins are complete (the frozen checker, the builder, every value under `environment`). It never authorizes a
new-evidence cell. This lets the instrument be run and inspected one cell at a time, K1a first, without
writing scenarios for cells whose fault schedules do not exist yet.

It loosens nothing for new evidence. A new-evidence cell needs this file final, and every instrument cell
as predicted under the same pre-registration commit as the new-evidence cell, so every instrument cell runs
again under the final commit and the batches run under a draft never count toward new evidence. Each
batch's authorization.json records the state it ran under, and verification checks it. Once a cell has an
authorized batch, any change to its entry is listed under Deviations with the batches it affects.

## The first discovery tranche (2026-10-17)

Declared before any batch of it ran. The matrix is not finalized at once: finishing every cell first would
need the fault mechanisms the new-evidence cells wait on (a client retry, toxics at a failpoint, a database
restart), the async landmark audit, and 27 more scenarios before the first question could be asked. The
tranche is the smallest set that asks one question legitimately.

- **Members.** C1-w1-sync, C3-w1-sync, C1-w5-sync, C3-w5-sync (the negative and placebo controls on both
  workloads under sync durability), K1a and K3 (the mechanisms N8 composes, each a published replication),
  K2 and K4 (control C2 and the third published replication, which the methodology requires before any new
  claim: C11), and N8, the only new-evidence cell.
- **Deferred.** Every other cell, each with its reason in `deferred`: the controls for workload variants no
  tranche cell runs, the misuse controls and K5, K1b (so N8 does not rest on its answer), the cells that
  need mechanisms the scenario model lacks, N7 and N11, and the async cells (A1-A4), whose landmark audit is
  pending. Their predictions stay as written, visible, and unchanged by this declaration.
- **Prior knowledge.** Once a new-evidence batch of the tranche exists, any change to a deferred cell's
  prediction is a Deviations entry that names that batch as prior knowledge. Predictions are never changed
  after their own cell's evidence is observed.
- **Widening.** Adding a cell is a new pre-registration commit. The instrument cells run again under it
  before its new evidence; batches of different commits never pool (the aggregator's rule), so nothing from
  this tranche is carried over as a qualifying batch.
- **Controls before N8.** C1-w1-sync, C3-w1-sync, C1-w5-sync, C3-w5-sync, K1a, K3, K2, and K4 as batches
  under the final commit, each as predicted, in one environment; C5 and C9 as controls reports from the
  canonical runner; AM1-AM10, determinism, C4, C6, C7, the fault self-test, and the oracle's independence as
  recorded. The combined case, concurrent writers and an effect in one real history, is not itself observed
  before N8: K1a validates the effect side and K3 the fork side, each on its own, and a control that
  observed both together would be N8.

## Rulings applied (2026-10-17)

- **N8 asks about a stale owner with uncommitted execute writes.** The schedule in the draft until now held
  A at L-EXC alone, after execute, with the charge fired. In every trial of K1a's passing batch
  (K1a-gh38964246590-1), the replacement's `put` at L-EXC returned before execute's `put_writes` returned:
  under sync durability LangGraph awaits a checkpoint put and not a task's writes, which commit in the
  background. A hold at L-EXC alone would therefore race B's read against A's writes committing, and the
  cell could not be deterministic. N8 now holds A twice, at L-EXW (`put_writes.before`, so the writes cannot
  commit) and at L-EXC (`put.before`, so the loop cannot go on; the first hold alone would not hold it, by
  the same histories), redelivers resume-1 only once both are held, and releases both only after B's
  `client.ok`. Every link is a recorded reference or a per-process order, never a clock. The question K1b
  asks, the committed-writes form, is deferred and N8 does not rest on its answer.
- **N8's I2 is NOT_EXERCISED, and `*` is no other anomaly.** Checker v1 exercises I2 only for a task whose
  committed writes were followed by an interruption: a stop of its worker, or its op ending in `client.fail`
  or `client.info` (`facts.interruption`, frozen). N8 has neither: A is held and released, never stopped, and
  both ops end in `client.ok`. K3's real histories, the same held takeover, show I2 absent in 20 of 20
  trials; K1a's show I2 exercised only through its kill. Narrower than NO_ANOMALY_UNSPECIFIED or
  NOT_EXERCISED, and justified without any N8-like run. `*`: PASS or NO_ANOMALY_UNSPECIFIED or
  NOT_EXERCISED, the same default every instrument cell carries.

## Rulings applied (2026-10-10)

My rulings on T-10's readings, applied here before the freeze:

- **D1.** A second run on the thread (a redelivery, a client's retry after `client.fail`, or concurrent
  writers under LGP-DT) never suppresses I1a, I1b, or I11. It still covers I2, I4, and I5-I8, which are about
  what a second run does. So K1a and N9 judge durability by PASS or a candidate, and the retry cells
  (N1-N4, N9) read I4 as NO_ANOMALY_UNSPECIFIED or GAP, never PASS.
- **D2.** I4 is PASS under LG-PW-EXC only where a node actually raised; otherwise NO_ANOMALY_UNSPECIFIED. No
  W1 or W5 cell predicts I4 PASS; the fault-free controls, N10, and A4 predict NO_ANOMALY_UNSPECIFIED.
- **P1.** A node whose `node.end` occurred before the stop was not running. A duplicate after a stop at L-EXW
  is GAP on I4 and I7a, with pending-write semantics named: K1a, A1, and N3 predict GAP, and #8039 is
  replicated on its observation, two charge rows with attempts 1 and 2.
- **P2.** K4 scores no outcome; #8764 is replicated on `checker.json`'s record of an accepted run with no
  durable record, in every valid trial.
- **P4.** K3 and N8 hold a worker at a barrier (a held pause, C2's shape: the writer set is concurrent by
  dispatch overlap) instead of SIGSTOP, which T-07 cannot schedule at a failpoint.
- **P5.** Arms name the audited occurrences: L-IN 1, L-RES 2 (approval's RESUME write, not the null task's
  copy), L-EXW 3, L-EXC 2, L-T1 3, and L-GET 1, which C2's barriers (K2, N5) use.
- **P6.** K1b, N4, A2, and A3's I2 cite LG-PW-CRASH: NO_ANOMALY_UNSPECIFIED when the committed writes are
  reused, GAP on a re-run.

## Rulings applied (2026-10-12)

- **C5, revised before finalization.** The original C5 measurement failed as specified
  (research/evidence/t-11/c5-original.json, kept as it was; 20 trials per mutant on W1 approve): M1 0 of
  20, M2 0 of 20, M3 20 of 20, M4 20 of 20, and M5 INVALID in every trial because checker v1's I3 alarm
  fires before I6b is judged; an exploratory run of M5 on W5 sequential, which did not count, was caught by
  I6b in 20 of 20. This is a failed instrument-design check before finalization, not a finding about
  LangGraph: M1 and M2 are not exercised by this matrix's workloads, and W1 is the wrong workload for M5.
  The first matrix's C5 is therefore M3 and M4 on W1 approve and M5 on W5 sequential, each caught by its
  intended invariant in at least 19 of 20 trials. M1 and M2 are out of scope for this matrix's instrument
  validation, recorded as NOT EXERCISED, and never counted or shown as passing; the C5 report names them
  beside its results, so three measured mutants are never read as five of five. The checker and I3 are
  unchanged, and no workload was added to exercise M1.

## Rulings applied (2026-10-16)

- **Every instrument cell predicts every invariant.** The aggregator reads an exercised invariant
  that a cell does not name as `recorded`, never as predicted, and an instrument cell counts only
  when it comes out as predicted. K1a's first formal batch showed the gap (Deviations). Each K-cell
  now says what it predicts for the invariants it does not name: K1a, K2, K3, K5, and K5-reject
  predict no other anomaly (`*`: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED); K4 predicts that
  nothing else is exercised (`*`: NOT_EXERCISED), since by P2 it scores no outcome. K2's I6a stays
  named as variable. The aggregator is unchanged and stays strict, and a test holds every instrument
  cell to full coverage under its rules.
- **K5 and K5-reject: I7c corrected to NO_ANOMALY_UNSPECIFIED, before either cell ran formally.** The
  audit found that checker v1 exercises I7c on w1-misuse (its approval effect is charge) and gives
  NO_ANOMALY_UNSPECIFIED, with approve and with reject: its unit tests on both, and T-10's validation
  on T-09's fault-free misuse histories (50 of 50). The NOT_EXERCISED of 2026-10-10 would have differed
  in every trial. I corrected it on the frozen checker's pre-freeze validation and unit tests, not on
  any batch of these cells: neither has run.

## Deferred from the first matrix

- **C5's M1 and M2.** The instrument's sensitivity to a lost pending-write row (M1) and to a by-id read
  that finds nothing (M2) is not measured by this matrix. M1's row is read back only after a crash that
  follows its commit, which is K1b's new-evidence question, so no control may run it; no workload here
  reads a checkpoint by id. Every results page that cites C5 states this limit.

- **E1 (K1a under exit).** Under exit durability LangGraph makes no `put_writes` call during a run and saves
  checkpoints and writes only when the loop exits, so L-EXW never occurs and there is no kill point to arm.
  No fault-free exit histories exist to derive one, and LG-DUR-EXIT is mapped to no invariant, so its
  duplicate could read only GAP. It comes back only with a landmark derived from audited exit-mode histories
  and an I4 rule under LG-DUR-EXIT, both before a freeze.

## Cells

| Cell | Phase | Evidence | Workload | Durability | Fault | Workers | N | Predicted | Observed | |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C1-w1-sync | instrument | control (C1) | w1 | sync | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - |  |
| C1-w1-async | instrument | control (C1) | w1 | async | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | deferred |
| C1-w1-exit | instrument | control (C1) | w1 | exit | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | deferred |
| C1-w1-reject-sync | instrument | control (C1) | w1 | sync | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | deferred |
| C1-w1-reject-async | instrument | control (C1) | w1 | async | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | deferred |
| C1-w1-misuse-sync | instrument | control (C1) | w1-misuse | sync | - | 1 | 50 | I4: EXPECTED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | deferred |
| C1-w1-misuse-async | instrument | control (C1) | w1-misuse | async | - | 1 | 50 | I4: EXPECTED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | deferred |
| C1-w5-sync | instrument | control (C1) | w5 | sync | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - |  |
| C1-w5-async | instrument | control (C1) | w5 | async | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | deferred |
| C3-w1-sync | instrument | control (C3) | w1 | sync | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - |  |
| C3-w1-reject-sync | instrument | control (C3) | w1 | sync | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | deferred |
| C3-w5-sync | instrument | control (C3) | w5 | sync | - | 1 | 50 | I4: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - |  |
| K2 | instrument | control (C2) | w5 | sync | F06 | 2 | 20 | I5a: GAP; I6a: NO_ANOMALY_UNSPECIFIED or GAP; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | anomaly on I5a: present |  |
| K5 | instrument | control (misuse) | w1-misuse | sync | - | 1 | 20 | I4: EXPECTED; I7c: NO_ANOMALY_UNSPECIFIED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | ledger precheck: 2 rows, attempts [1, 2]; ledger charge: 1 row, attempts [1] | deferred |
| K5-reject | instrument | control (misuse) | w1-misuse | sync | - | 1 | 20 | I4: EXPECTED; I7c: NO_ANOMALY_UNSPECIFIED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | ledger precheck: 2 rows, attempts [1, 2]; ledger charge: 0 rows, attempts [] | deferred |
| K1a | instrument | replication (langgraph#8039) | w1 | sync | F01 | 2 | 20 | I4: GAP; I7a: GAP; I1b: PASS; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | ledger charge: 2 rows, attempts [1, 2] |  |
| K3 | instrument | replication (langgraph#8796) | w5 | sync | F05 | 2 | 20 | I5a: GAP; I6a: GAP; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | anomaly on I5a: present |  |
| K4 | instrument | replication (langgraph#8764) | w1 | sync | F01 | 1 | 20 | I1a: NOT_EXERCISED; I1b: NOT_EXERCISED; *: NOT_EXERCISED | checker observation 'accepted run with no durable record: op start-1 ended': 1 |  |
| K1b | new-evidence | new-evidence | w1 | sync | F01 | 2 | 20 | I2: NO_ANOMALY_UNSPECIFIED or GAP | - | deferred |
| N1 | new-evidence | new-evidence | w1 | sync | F11 | 1 | 20 | I4: GAP; I7a: GAP | - | unknown, deferred |
| N2 | new-evidence | new-evidence | w1 | sync | F11 | 1 | 20 | I4: NO_ANOMALY_UNSPECIFIED or GAP; I11: NO_ANOMALY_UNSPECIFIED | - | unknown, deferred |
| N3 | new-evidence | new-evidence | w1 | sync | F10 | 1 | 20 | I4: GAP | - | deferred |
| N4 | new-evidence | new-evidence | w1 | sync | F11 | 1 | 20 | I2: NO_ANOMALY_UNSPECIFIED or GAP | - | unknown, deferred |
| N5 | new-evidence | new-evidence | w1 | sync | F08 | 2 | 20 | I5a: GAP; I7a: GAP; I7c: GAP; I8: GAP | - | deferred |
| N6 | new-evidence | new-evidence | w1 | sync | F08 | 2 | 20 | I5a: GAP | - | deferred |
| N7 | new-evidence | new-evidence | w1 | sync | F07 | 1 | 20 | I4: GAP | - | unknown, deferred |
| N8 | new-evidence | new-evidence | w1 | sync | F05 | 2 | 20 | I4: GAP; I5a: GAP; I7a: GAP; I6a: GAP; I2: NOT_EXERCISED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - |  |
| N9 | new-evidence | new-evidence | w1 | sync | F13 | 1 | 20 | I1b: PASS | - | deferred |
| N10 | new-evidence | new-evidence | w1 | sync | F12 | 1 | 20 | I1a: PASS; I1b: PASS; I11: PASS; I4: NO_ANOMALY_UNSPECIFIED | - | deferred |
| N11 | new-evidence | new-evidence | w1 | sync | F01 | 2 | 20 | I7c: PASS or NO_ANOMALY_UNSPECIFIED | - | unknown, deferred |
| N12 | new-evidence | new-evidence | w5 | sync | F11 | 1 | 20 | I5: NO_ANOMALY_UNSPECIFIED | - | unknown, deferred |
| N12b | new-evidence | new-evidence | w5 | sync | F10 | 1 | 20 | I5b: GAP | - | deferred |
| A1 | new-evidence | new-evidence | w1 | async | F01 | 2 | 20 | I4: GAP; I7a: GAP; I1b: NOT_EXERCISED or EXPECTED | - | pending, deferred |
| A2 | new-evidence | new-evidence | w1 | async | F01 | 2 | 20 | I2: NO_ANOMALY_UNSPECIFIED or GAP | - | pending, deferred |
| A3 | new-evidence | new-evidence | w1 | async | F11 | 1 | 20 | I2: NO_ANOMALY_UNSPECIFIED or GAP; I11: NO_ANOMALY_UNSPECIFIED | - | unknown, pending, deferred |
| A4 | new-evidence | new-evidence | w1 | async | F12 | 1 | 20 | I4: NO_ANOMALY_UNSPECIFIED; *: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED | - | pending, deferred |

## Deviations

Any change to a cell's entry after it has an authorized batch, and any change to this file after it is
final, is listed here with its reason and the batches it affects. A change to a deferred cell's prediction
after a new-evidence batch of the tranche exists names that batch as prior knowledge.

- **K1a: the `*` prediction, added 2026-10-16, after a batch.** Batch K1a-gh38926456031-1 (run
  commit f4ca5f9, pre-registration 68704ff, Actions run 38926456031) came out as predicted on I4,
  I7a, I1b, and the ledger observation in 20 of 20 trials, but K1a named no other invariant, so the 11
  others the checker exercised were recorded without a prediction and the cell read `recorded`, not
  as predicted; the gate refused it as an instrument cell. `*`: PASS or NO_ANOMALY_UNSPECIFIED or
  NOT_EXERCISED (no anomaly beyond K1a's predicted behavior) was added only after that batch exposed
  the gap. The batch stays archived as it ran, under the pre-registration it ran under, as the run
  that found the defect; it is never counted as a passing replication. K1a runs again as a new batch.

## Pre-registration data

```json
{
  "builder": {
    "path": "src/crashcourt/orchestrator/declared.py",
    "sha256": "7a9a685fe8ae28b4eec8e8587b667a2ffabdb1e738d09665851883fdd790226e"
  },
  "cells": [
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-sync",
      "master_seed": "C1-w1-sync-v1",
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly on any invariant"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w1-sync.yaml",
      "scenario_sha256": "6da274e0f9b03561422d2a966c11fe62253191aa2eda4ea2adc85a94fe32d271",
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": "no cell of the first discovery tranche runs this workload variant or durability, so this control gates nothing in it",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-async",
      "master_seed": null,
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly on any invariant"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w1-async.yaml",
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": "no cell of the first discovery tranche runs this workload variant or durability, so this control gates nothing in it",
      "deterministic": true,
      "durability": "exit",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-exit",
      "master_seed": null,
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly on any invariant"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w1-exit.yaml",
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": "no cell of the first discovery tranche runs this workload variant or durability, so this control gates nothing in it",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-reject-sync",
      "master_seed": null,
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly on any invariant"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w1-reject-sync.yaml",
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms with the resume rejected",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": "no cell of the first discovery tranche runs this workload variant or durability, so this control gates nothing in it",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-reject-async",
      "master_seed": null,
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly on any invariant"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w1-reject-async.yaml",
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms with the resume rejected",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": "no cell of the first discovery tranche runs this workload variant or durability, so this control gates nothing in it",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-misuse-sync",
      "master_seed": null,
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "(b): the effect before interrupt() re-runs on resume (LG-INT-RERUN)"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no other anomaly"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w1-misuse-sync.yaml",
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w1-misuse"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": "no cell of the first discovery tranche runs this workload variant or durability, so this control gates nothing in it",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-misuse-async",
      "master_seed": null,
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "(b): the effect before interrupt() re-runs on resume (LG-INT-RERUN)"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no other anomaly"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w1-misuse-async.yaml",
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w1-misuse"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w5-sync",
      "master_seed": "C1-w5-sync-v1",
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly on any invariant"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w5-sync.yaml",
      "scenario_sha256": "00b91f086eca8dcef9f23f52f7d0847072336d6a57ebb8a73c46f3bfc9f8ede2",
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deferred": "no cell of the first discovery tranche runs this workload variant or durability, so this control gates nothing in it",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w5-async",
      "master_seed": null,
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly on any invariant"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C1-w5-async.yaml",
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "C3",
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly, or a missing fp.hit for an arm",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C3-w1-sync",
      "master_seed": "C3-w1-sync-v1",
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "identical to C1: no anomaly; fp.hit for every arm"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C3-w1-sync.yaml",
      "scenario_sha256": "1fa27050bb16d28bef997d115feacd66d5a7359260e69f51c8f7173c0b57323b",
      "trigger": "every failpoint the new-evidence cells arm on this workload, at the same landmarks and audited occurrences, armed with action none; a latency toxic of 0 ms and 0 jitter on each worker's proxy for the whole trial",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C3",
      "deferred": "no cell of the first discovery tranche runs this workload variant or durability, so this control gates nothing in it",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly, or a missing fp.hit for an arm",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C3-w1-reject-sync",
      "master_seed": null,
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "identical to C1: no anomaly; fp.hit for every arm"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C3-w1-reject-sync.yaml",
      "scenario_sha256": null,
      "trigger": "every failpoint the new-evidence cells arm on this workload, at the same landmarks and audited occurrences, armed with action none; a latency toxic of 0 ms and 0 jitter on each worker's proxy for the whole trial",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C3",
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly, or a missing fp.hit for an arm",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C3-w5-sync",
      "master_seed": "C3-w5-sync-v1",
      "n": 50,
      "observations": [],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "identical to C1: no anomaly; fp.hit for every arm"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/C3-w5-sync.yaml",
      "scenario_sha256": "539cde9bcbed9cae37e875180dc6d863efb2b5262afc99fc9f0e02738c2ac11c",
      "trigger": "every failpoint the new-evidence cells arm on this workload, at the same landmarks and audited occurrences, armed with action none; a latency toxic of 0 ms and 0 jitter on each worker's proxy for the whole trial",
      "workers": 1,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "C2",
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any trial without a fork",
      "evidence": "control",
      "fault": "F06",
      "hypothesis": "H1",
      "id": "K2",
      "master_seed": "K2-v1",
      "n": 20,
      "observations": [
        {
          "invariant": "I5a",
          "note": "the fork, whatever its class",
          "present": true,
          "source": "anomaly"
        }
      ],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I5a",
          "note": "fork in every trial"
        },
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I6a",
          "note": "C2 forces the fork, not which branch the acknowledgments and the head agree on; either class, the same in every trial (alternation makes the cell UNSTABLE)"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly beyond the named ones; completeness audit of 2026-10-16, before any batch of this cell"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/K2.yaml",
      "scenario_sha256": "428c387ed96d79d88ad394f028d3393973c6d36798a066df52b3ad2cffe1c4b2",
      "trigger": "both workers held at L-GET (occurrence 1, get_tuple.after) for their turn ops, released together",
      "workers": 2,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "misuse",
      "deferred": "the misuse controls gate no cell of the first discovery tranche; deferred with the misuse workload",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any non-EXPECTED I4",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "K5",
      "master_seed": null,
      "n": 20,
      "observations": [
        {
          "attempts": [
            1,
            2
          ],
          "effect": "precheck",
          "note": "attempt 1 before interrupt() in start-1, attempt 2 when approval re-runs on resume",
          "rows": 2,
          "source": "ledger"
        },
        {
          "attempts": [
            1
          ],
          "effect": "charge",
          "note": null,
          "rows": 1,
          "source": "ledger"
        }
      ],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "(b) for precheck under LG-INT-RERUN"
        },
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I7c",
          "note": "checker v1 exercises I7c on w1-misuse, whose approval effect is charge; corrected from NOT_EXERCISED on 2026-10-16, before any batch of this cell"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly beyond the named ones; completeness audit of 2026-10-16, before any batch of this cell"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/K5.yaml",
      "scenario_sha256": null,
      "trigger": "no fault; one resume (approve)",
      "workers": 1,
      "workload": "w1-misuse"
    },
    {
      "confidence": "predicted",
      "control": "misuse",
      "deferred": "the misuse controls gate no cell of the first discovery tranche; deferred with the misuse workload",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any non-EXPECTED I4, or any charge row",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "K5-reject",
      "master_seed": null,
      "n": 20,
      "observations": [
        {
          "attempts": [
            1,
            2
          ],
          "effect": "precheck",
          "note": null,
          "rows": 2,
          "source": "ledger"
        },
        {
          "attempts": [],
          "effect": "charge",
          "note": null,
          "rows": 0,
          "source": "ledger"
        }
      ],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "(b): two precheck rows, zero charge"
        },
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I7c",
          "note": "checker v1 exercises I7c on w1-misuse, whose approval effect is charge; corrected from NOT_EXERCISED on 2026-10-16, before any batch of this cell"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly beyond the named ones; completeness audit of 2026-10-16, before any batch of this cell"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/K5-reject.yaml",
      "scenario_sha256": null,
      "trigger": "no fault; one resume (reject)",
      "workers": 1,
      "workload": "w1-misuse"
    },
    {
      "confidence": "predicted",
      "control": "C11",
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "the observation in fewer than 20 of 20 valid trials, or any outcome other than predicted: a harness investigation, never a finding",
      "evidence": "replication",
      "fault": "F01",
      "hypothesis": "H4",
      "id": "K1a",
      "master_seed": "K1a-v1",
      "n": 20,
      "observations": [
        {
          "attempts": [
            1,
            2
          ],
          "effect": "charge",
          "note": "langgraph#8039's published observation: attempt 1 charged before the kill, attempt 2 after the redelivery",
          "rows": 2,
          "source": "ledger"
        }
      ],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I4",
          "note": "P1: execute had ended at the kill, so it was not running; pending-write semantics own the re-run, and the reason names them"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I7a",
          "note": "P1: execute had ended at the kill, so it was not running; pending-write semantics own the re-run, and the reason names them"
        },
        {
          "expect": [
            "PASS"
          ],
          "invariant": "I1b",
          "note": "D1: the redelivery does not suppress I1b; every acknowledged checkpoint survives"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly beyond K1a's predicted behavior; added on 2026-10-16 after batch K1a-gh38926456031-1 exposed the gap (Deviations)"
        }
      ],
      "replication_of": "langgraph#8039",
      "scenario": "scenarios/first-matrix/K1a.yaml",
      "scenario_sha256": "cfec4076e08f9daa555745f82c32f521012ab1889e8d5d332fb657e5448c5ee7",
      "trigger": "SIGKILL at L-EXW (occurrence 3 of resume-1's put_writes), put_writes.before: execute has ended and its writes are not committed; resume-1 redelivered to the worker's replacement",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C11",
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "the fork in fewer than 20 of 20 valid trials: a harness investigation, never a finding",
      "evidence": "replication",
      "fault": "F05",
      "hypothesis": "H3",
      "id": "K3",
      "master_seed": "K3-v1",
      "n": 20,
      "observations": [
        {
          "invariant": "I5a",
          "note": "langgraph#8796's published observation: two processes on one thread fork the lineage",
          "present": true,
          "source": "anomaly"
        }
      ],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I5a",
          "note": "fork"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I6a",
          "note": "concurrent writers: the head's writer and the larger id are named (R3)"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly beyond the named ones; completeness audit of 2026-10-16, before any batch of this cell"
        }
      ],
      "replication_of": "langgraph#8796",
      "scenario": "scenarios/first-matrix/K3.yaml",
      "scenario_sha256": "8d270b489c0f8b411cc42e3c1004e0c648a5310bb48536ca2b423d62953a7db3",
      "trigger": "A held at L-T1 (occurrence 3 of turn-1's put: after respond), put.before; turn-1 redelivered to B; after B's client.ok, A released: a held pause in C2's shape, not SIGSTOP",
      "workers": 2,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "C11",
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "the observation in fewer than 20 of 20 valid trials, or any row for the thread: a harness investigation, never a finding",
      "evidence": "replication",
      "fault": "F01",
      "hypothesis": null,
      "id": "K4",
      "master_seed": "K4-v1",
      "n": 20,
      "observations": [
        {
          "count": 1,
          "note": "langgraph#8764's published observation: the run was accepted and the snapshot holds no row for its thread",
          "source": "checker",
          "starts_with": "accepted run with no durable record: op start-1 ended"
        }
      ],
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NOT_EXERCISED"
          ],
          "invariant": "I1a",
          "note": "no checkpoint was written, so nothing is scored (R1)"
        },
        {
          "expect": [
            "NOT_EXERCISED"
          ],
          "invariant": "I1b",
          "note": "no checkpoint was acknowledged"
        },
        {
          "expect": [
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "P2: K4 scores no outcome, and the frozen checker exercises no invariant on this shape; completeness audit of 2026-10-16, before any batch of this cell"
        }
      ],
      "replication_of": "langgraph#8764",
      "scenario": "scenarios/first-matrix/K4.yaml",
      "scenario_sha256": "d0395f1ef44a79e929aadce39b6403cb9cb8d3cdef6359387876bb41b2abde4d",
      "trigger": "SIGKILL at L-IN (occurrence 1 of start-1's put), put.before",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "the committed-writes form of the stale-owner question; deferred so that N8, which holds the writes uncommitted, does not rest on its answer",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any re-run",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": "H5",
      "id": "K1b",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I2",
          "note": "LG-PW-CRASH (decision 2): NO_ANOMALY_UNSPECIFIED when the committed writes are reused, GAP on a re-run"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/K1b.yaml",
      "scenario_sha256": null,
      "trigger": "SIGKILL at L-EXW (occurrence 3 of resume-1's put_writes), put_writes.after (writes committed); resume-1 redelivered",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deferred": "needs a client retry after client.fail, a schedule step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "VIOLATION_CANDIDATE on any invariant",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": "H7",
      "id": "N1",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I4",
          "note": "(c): two resumes on one thread"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I7a",
          "note": "orphan"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N1.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-RES (occurrence 2 of resume-1's put_writes: approval's RESUME write, not the null task's copy), put_writes.before; downstream timeout toxic on A's proxy; release; heal after a fixed delay; the client retries resume-1 once as resume-2 after client.fail",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deferred": "needs a client retry after client.fail, a schedule step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "a duplicate or a fork",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": "H7",
      "id": "N2",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I4",
          "note": "no duplicate, or a duplicate on the retried thread; D1: the retry is a second resume, so I4 has no PASS"
        },
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I11",
          "note": "the client saw fail"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N2.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXC (occurrence 2 of resume-1's put), put.before; downstream timeout; release; heal after a fixed delay; retry as resume-2",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "needs a client retry after client.fail, a schedule step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "not 20 of 20 GAP",
      "evidence": "new-evidence",
      "fault": "F10",
      "hypothesis": null,
      "id": "N3",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I4",
          "note": "P1: execute had ended before its writes failed, so the re-run is pending-write semantics' case; D1: the retry is a second resume"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N3.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXW (occurrence 3 of resume-1's put_writes), put_writes.before; upstream reset_peer; release; retry",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deferred": "needs a client retry after client.fail, a schedule step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any re-run",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": "H5",
      "id": "N4",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I2",
          "note": "LG-PW-CRASH (decision 2): NO_ANOMALY_UNSPECIFIED when the committed writes are reused, GAP on a re-run"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N4.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXW (occurrence 3 of resume-1's put_writes), put_writes.before; downstream timeout; release; heal after a fixed delay; retry",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "needs a release triggered by a put_writes.return, a step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "UNSTABLE (1 to 19 of 20)",
      "evidence": "new-evidence",
      "fault": "F08",
      "hypothesis": "H2",
      "id": "N5",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I5a",
          "note": "fork at the interrupt checkpoint"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I7a",
          "note": "orphan if B's checkpoint is the head"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I7c",
          "note": "approval mismatch"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I8",
          "note": null
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N5.yaml",
      "scenario_sha256": null,
      "trigger": "A and B held at L-GET (occurrence 1, get_tuple.after) for resume-1 (approve, A) and resume-2 (reject, B); release A; on A's L-RES (occurrence 2) put_writes.return, release B",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "needs a release triggered by a put_writes.return, a step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "UNSTABLE",
      "evidence": "new-evidence",
      "fault": "F08",
      "hypothesis": "H2",
      "id": "N6",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I5a",
          "note": "the mirror of N5; which branch heads the thread follows the release order"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N6.yaml",
      "scenario_sha256": null,
      "trigger": "as N5 with the release order swapped",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deferred": "a second resume to the same worker after client.ok (F07): outside the first discovery tranche, which asks one question",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "UNSTABLE",
      "evidence": "new-evidence",
      "fault": "F07",
      "hypothesis": null,
      "id": "N7",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I4",
          "note": "(c), or a no-op with the same state"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N7.yaml",
      "scenario_sha256": null,
      "trigger": "resume-1 (approve) to A, client.ok; then resume-2 (approve) to A",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "UNSTABLE, or any anomaly beyond the predicted ones",
      "evidence": "new-evidence",
      "fault": "F05",
      "hypothesis": "H3",
      "id": "N8",
      "master_seed": "N8-v1",
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I4",
          "note": "two charge rows, attempts 1 and 2"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I5a",
          "note": null
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I7a",
          "note": "orphan on the losing branch"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I6a",
          "note": "head by clock"
        },
        {
          "expect": [
            "NOT_EXERCISED"
          ],
          "invariant": "I2",
          "note": "checker v1 exercises I2 only after a stop of the worker or an op ending in client.fail or client.info (facts.interruption); N8 has neither: A is held, not stopped, and both ops end in client.ok. K3's real histories (a held takeover) show I2 absent in 20 of 20; K1a's show it exercised only through the kill"
        },
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no anomaly beyond N8's predicted behavior"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N8.yaml",
      "scenario_sha256": "66c9981eda147a69a74f556be4822f2ca06aba346523b07ee35e0837ad8410dc",
      "trigger": "A held twice at the calls after execute, with the charge already fired: at L-EXW (occurrence 3 of resume-1's put_writes), put_writes.before, so execute's writes cannot commit, and at L-EXC (occurrence 2 of resume-1's put), put.before, so A's loop cannot go on; with both held, resume-1 redelivered to B; after B's client.ok, both released: a held pause in C2's shape, not SIGSTOP",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "needs a database restart at a failpoint and a client retry, steps the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any acknowledged checkpoint missing",
      "evidence": "new-evidence",
      "fault": "F13",
      "hypothesis": "H8",
      "id": "N9",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "PASS"
          ],
          "invariant": "I1b",
          "note": "every earlier acknowledged checkpoint survives; D1: the retry does not suppress I1b"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N9.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXC (occurrence 2 of resume-1's put), put.before; database restart; release; the put fails; retry as resume-2",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "needs a toxic injected and healed at a failpoint (F12), a step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly (VIOLATION_CANDIDATE by the F12 rule)",
      "evidence": "new-evidence",
      "fault": "F12",
      "hypothesis": null,
      "id": "N10",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "PASS"
          ],
          "invariant": "I1a",
          "note": null
        },
        {
          "expect": [
            "PASS"
          ],
          "invariant": "I1b",
          "note": null
        },
        {
          "expect": [
            "PASS"
          ],
          "invariant": "I11",
          "note": null
        },
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N10.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXC (occurrence 2 of resume-1's put), put.before; downstream latency 24 h; release; heal after a fixed delay",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deferred": "a kill with committed resume writes under reject (F01 on W1 reject): outside the first discovery tranche, which asks one question",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any charge row",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": null,
      "id": "N11",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "PASS",
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I7c",
          "note": "zero charge rows"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N11.yaml",
      "scenario_sha256": null,
      "trigger": "W1 with the resume rejected; SIGKILL at L-RES (occurrence 2 of resume-1's put_writes), put_writes.after (resume committed); redeliver",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deferred": "needs a toxic injected and healed at a failpoint (F11), a step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "a dangling parent or a fork",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": null,
      "id": "N12",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I5",
          "note": "the lost-ack put committed, so turn-2's parent exists"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N12.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-T1 (occurrence 3 of turn-1's put), put.before; downstream timeout; release; heal; turn-2 to the same worker",
      "workers": 1,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "needs a toxic injected at a failpoint (F10), a step the scenario model does not have (T-12's scope); a later tranche",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "n/a: recorded",
      "evidence": "new-evidence",
      "fault": "F10",
      "hypothesis": null,
      "id": "N12b",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I5b",
          "note": "dangling parent with a single writer"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N12b.yaml",
      "scenario_sha256": null,
      "trigger": "as N12 with reset_peer upstream",
      "workers": 1,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "the landmark occurrences under async are not audited (pending); no async cell is in the first discovery tranche",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "not as predicted",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": "H4",
      "id": "A1",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": "the landmark's occurrence under async, from audited fault-free async histories (T-09's audited corpus is sync only)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I4",
          "note": "P1: execute had ended at the kill, so it was not running; pending-write semantics own the re-run, and the reason names them"
        },
        {
          "expect": [
            "GAP"
          ],
          "invariant": "I7a",
          "note": "P1: execute had ended at the kill, so it was not running; pending-write semantics own the re-run, and the reason names them"
        },
        {
          "expect": [
            "NOT_EXERCISED",
            "EXPECTED"
          ],
          "invariant": "I1b",
          "note": "the crash is witnessed"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/A1.yaml",
      "scenario_sha256": null,
      "trigger": "K1a under async: SIGKILL at L-EXW (occurrence 3 of resume-1's put_writes), put_writes.before; resume-1 redelivered",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "the landmark occurrences under async are not audited (pending); no async cell is in the first discovery tranche",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any re-run",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": null,
      "id": "A2",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": "the landmark's occurrence under async, from audited fault-free async histories (T-09's audited corpus is sync only)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I2",
          "note": "LG-PW-CRASH (decision 2): NO_ANOMALY_UNSPECIFIED when the committed writes are reused, GAP on a re-run"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/A2.yaml",
      "scenario_sha256": null,
      "trigger": "K1b under async: SIGKILL at L-EXW (occurrence 3 of resume-1's put_writes), put_writes.after; resume-1 redelivered",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deferred": "the landmark occurrences under async are not audited (pending); no async cell is in the first discovery tranche",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any re-run",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": null,
      "id": "A3",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": "the landmark's occurrence under async, from audited fault-free async histories (T-09's audited corpus is sync only)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I2",
          "note": "LG-PW-CRASH (decision 2): NO_ANOMALY_UNSPECIFIED when the committed writes are reused, GAP on a re-run"
        },
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I11",
          "note": "no clause under async"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/A3.yaml",
      "scenario_sha256": null,
      "trigger": "N4 under async: pause at L-EXW (occurrence 3 of resume-1's put_writes), put_writes.before; downstream timeout; release; heal after a fixed delay; retry",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deferred": "the landmark occurrences under async are not audited (pending); no async cell is in the first discovery tranche",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly",
      "evidence": "new-evidence",
      "fault": "F12",
      "hypothesis": null,
      "id": "A4",
      "master_seed": null,
      "n": 20,
      "observations": [],
      "pending": "the landmark's occurrence under async, from audited fault-free async histories (T-09's audited corpus is sync only)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I4",
          "note": "D2: no node raised, so LG-PW-EXC gives no PASS"
        },
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "NOT_EXERCISED"
          ],
          "invariant": "*",
          "note": "no other clause applies under async"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/A4.yaml",
      "scenario_sha256": null,
      "trigger": "N10 under async: pause at L-EXC (occurrence 2 of resume-1's put), put.before; downstream latency 24 h; release; heal after a fixed delay",
      "workers": 1,
      "workload": "w1"
    }
  ],
  "checker": {
    "checker_sha256": "d80951681c2b8656a08647d3ba60ee3b9b025e1ff118d83796ccdb433f111097",
    "effect_models": {
      "w1": "7e0376323ac016537edf5b1abb4641efba36519d40df58d9762817158e857859",
      "w1-misuse": "d750e43ba865dc834a7341def0e19727bec72e5fd96da2ddf4ec7d2f467b1b5f",
      "w5": "74d96b6a84043e5cbd7ef8e3386bba50c191572dff16284bb1664e991dc6f8af"
    },
    "frozen_md_sha256": "ddff87d4d487ef211d385279951eaa2be37c71394956fcc0c4ff541667338e12",
    "landmark_tables": {
      "langgraph-v1": "279e5e441b14dafca556d8b8824afbcc4698ce0d073a63f324386956160682f4"
    },
    "registry_sha256": "0c25b2d96620a0751580e3d904cba8d81040d8d2b4cb007826ae2ceab46eb5c5",
    "version": "v1"
  },
  "environment": {
    "clause_registry_sha256": "0c25b2d96620a0751580e3d904cba8d81040d8d2b4cb007826ae2ceab46eb5c5",
    "git_dirty": false,
    "lockfile_sha256": "19a0dd0df75a86126dec1b617422001fedb30ac18978c7fe31585ce52a92702e",
    "os_system": "Linux",
    "postgres.fsync": "on",
    "postgres.synchronous_commit": "on",
    "postgres.track_commit_timestamp": "on",
    "runner": "github-hosted",
    "spec_sha256": "c6560f855c0bf80d9d1b18cd1407157dd619d84bc7e2b14dd7e1591f3cb45258"
  },
  "instrument_checks": [
    {
      "evidence": null,
      "id": "AM1-AM10",
      "requirement": "the ten artifact mutants give exactly their specified outcomes (10 of 10) at the freeze commit"
    },
    {
      "evidence": null,
      "id": "C5",
      "requirement": "Saver mutants, as revised before finalization after the original measurement failed (M1 0 of 20, M2 0 of 20, M5 on W1 INVALID by the I3 alarm; research/evidence/t-11/c5-original.json): M3 on W1 approve caught by I5a, M4 on W1 approve caught by I1b, and M5 on W5 sequential caught by I6b, each in at least 19 of 20 trials. M1 and M2 are NOT EXERCISED by the first matrix's workloads, out of scope for its instrument validation, and never counted or shown as passing: M1's dropped row is read back only after a crash that follows its commit, which is K1b's new-evidence question, and no first-matrix workload reads a checkpoint by id (M2)."
    },
    {
      "evidence": null,
      "id": "determinism",
      "requirement": "the checker run twice on 20 trials gives byte-identical output"
    },
    {
      "evidence": null,
      "id": "C4",
      "requirement": "disarmed transparency at the pinned versions: identical conformance results and normalized state hashes"
    },
    {
      "evidence": null,
      "id": "C9",
      "requirement": "Sequential reference: for each single-writer, fault-free C1 cell of the tranche under sync durability (C1-w1-sync and C1-w5-sync in the first discovery tranche), every trial of one canonical batch at the cell's N reaches the same final state as the same operation sequence run on InMemorySaver in one process: the head checkpoint's channels as the frozen checker's reader decodes them from the trial's snapshot (W1: order, plan, resume, decision, executed; W5: messages as type, id, and content), with the trial's thread id normalized away."
    },
    {
      "evidence": null,
      "id": "C6",
      "requirement": "the clause registry validates offline at the run commit (crashcourt clauses check)"
    },
    {
      "evidence": null,
      "id": "C7",
      "requirement": "batches pool only across identical environments: the fingerprint rule, with every exception recorded in research/compatibility.json"
    },
    {
      "evidence": null,
      "id": "fault-selftest",
      "requirement": "every fault primitive passes its self-test (crashcourt faults selftest) in the environment the batches run in"
    },
    {
      "evidence": null,
      "id": "oracle-independence",
      "requirement": "doctor shows the effect sink and the oracle connection are not behind Toxiproxy, and the sink reports no test hooks and the manifest's source hash"
    }
  ],
  "max_invalid_share": 0.02,
  "name": "first-matrix",
  "seeds": {
    "derivation": "sha256(json([master_seed, cell, trial]))",
    "master_seed_in": "scenario"
  },
  "state": "draft",
  "tranche": {
    "name": "first-discovery",
    "note": "The smallest tranche that answers one new-evidence question, N8, under the controls and replications its mechanisms rest on: C1 and C3 on both workloads under sync durability, K1a (redelivery with uncommitted writes and a duplicate effect) and K3 (a held takeover and a stale write), and K2 and K4, which the methodology requires before any new claim (C2, C11). Declared 2026-10-17, before any batch of the tranche."
  },
  "v": 1
}
```
