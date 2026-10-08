# Gate note — G-1 · 2026-10-08

1. What was built: no code; G-1 is the differentiation gate. The record is research/gates/g-1.md (2026-09-26, T-00),
   which maps the existing work, answers the two blocking questions, and says what this project reuses, extends, or
   leaves alone. It was re-checked on 2026-10-08, before the T-01 merge.

2. Why: the gate asks whether a genuine equivalent of this lab already exists. If one did, the right move would be
   to contribute to it or narrow the thesis to what it leaves uncovered, not to build a duplicate (spec section 28).

3. Important alternatives, and why they lost:
   - Contribute to mstevens843/crashpoint instead. It measures crash-to-effect exactly-once at named barriers across
     five engines, with an out-of-process ledger, but has no network or database faults, ambiguous commits,
     multi-writer cells, lineage invariants, or resume scenarios. Its outcome names and its #8039 cell are reused;
     the rest is a different question.
   - Build only on #8796's write-ownership capability. It covers one capability, with SIGSTOP and embedded Postgres.
     Its clauses are reused as C10's specification and its fork as a C11 replication target, never duplicated.
   - Pivot the thesis to an uncovered remainder. Not needed: the remainder is the core of V1.

4. What failed or surprised me: the working name collided with mstevens843/crashpoint, which T-00 resolved with the
   rename. Since the record, the LangGraph tracker has gained single-scenario reports close to the planned
   human-in-the-loop and effect-ledger cells, such as #9185 (resuming after a tool error re-runs the tool and repeats
   its side effect) and #9106 (resuming one of two parallel interrupts re-runs a completed sibling). None is a harness,
   but together they raise the bar for any claim of novelty.

5. How the evidence supports the conclusion:
   - The record and its sources: research/gates/g-1.md.
   - Re-check on 2026-10-08:
     - https://github.com/langchain-ai/langgraph/issues/8796 is open and unassigned; last activity 2026-09-15.
     - https://github.com/mstevens843/crashpoint was last pushed on 2026-09-24, before the record.
     - https://github.com/langchain-ai/langgraph/issues/8039, /8234, and /8764 are open; they remain prior art and C11
       replication targets.
     - LangGraph issues opened since 2026-09-25 in this area (persistence, interrupts and resume, error handling, side
       effects: #9076, #9078, #9099, #9106, #9185, #9218, #9234) come from seven different reporters, each describing
       one scenario.
     - A GitHub repository search for LangGraph checkpointer fault, chaos, crash, or conformance projects created
       since 2026-09-20 found application demos and new persistence integrations, and no test harness.

Decision: PASS. GO on differentiation and continued investment in Crashcourt.

Crashcourt has enough demonstrated differentiation to continue. The current landscape does not appear to contain a
project that combines the planned fault model, real Postgres execution, an independent effect ledger, correlation of
lineage with effects, and cross-runtime ownership analysis in one reproducible conformance harness.

This decision does not claim that T-01 is technically complete, that the benchmark is validated, that any planned
finding is novel, or that any upstream behavior is a confirmed bug.

Conditions:
- Overlapping prior art keeps being credited.
- Existing and future LangGraph issues that cover individual scenarios are prior art or replication targets, never
  new findings.
- Any claim of novelty needs a fresh prior-art check when the finding is written up for publication, and G4 repeats
  this check at every gate.
