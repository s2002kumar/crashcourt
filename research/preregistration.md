# Pre-registration: the first fault matrix

**Status: DRAFT.** This file authorizes nothing. `crashcourt experiment authorize` refuses every batch until
`state` is `final`, which needs everything under "Before this is final" and my commit.

This file fixes, before any batch of the first matrix runs, what each cell runs, how many trials it gets, and
what I predict it shows. The commit that last changes it is the pre-registration commit. A batch runs only
from a descendant of that commit, under the checker it names, and the summary shows every cell's prediction
beside what it observed. Deviations are logged below, never fixed silently.

## What is binding

The JSON block at the end is the binding part; the prose and the table explain it.

- **The checker.** `checker` names the frozen checker by two hashes: the checker files'
  (`checker_sha256`), and `src/crashcourt/checker/FROZEN.md`'s bytes (`frozen_md_sha256`), which also pin the
  clause registry, the workloads' effect models, and every scenario's landmark table. The commit that last
  changed FROZEN.md must be an ancestor of this file's commit.
- **The cells.** Each cell names its scenario file and that scenario's content hash, its master seed, N, its
  phase, and its predicted outcome per invariant. One prediction value is firm; several mean one of them, the
  same in every trial; `*` covers every invariant the cell does not name. `NOT_EXERCISED` predicts that the
  invariant is omitted from the results. Cells whose outcome is honestly unknown say `unknown`; their
  predictions record the expected shape and are compared, but not flagged.
- **The environment.** Every batch's manifest must hold each value under `environment`.
- **Instrument checks.** Checks that are not batches (artifact and saver mutants, checker determinism,
  transparency, the sequential reference) each need recorded evidence before this is final.

## How it is enforced

- `crashcourt experiment authorize <scenario> --trials N` runs before doctor and refuses unless the checkout
  is clean; this file is final and committed; the checker is FROZEN and verifies against FROZEN.md, whose
  hash and checker hash are the ones named here; the scenario's hash, cell, and master seed match its cell;
  N is the cell's N; the scenario's landmark table verifies against the freeze; and, for a new-evidence
  cell, every instrument cell has come out as predicted. It writes `authorization.json` into the batch.
- After doctor, the batch's manifest must hold the pre-registered environment, name the authorized commit,
  and record a clean checkout.
- `crashcourt experiment verify <batch>` re-proves an archived batch from git: this file's commit is an
  ancestor of the run commit and did not change before it, the freeze commit is an ancestor of this file's
  commit, every hash matches, there are exactly N trials, and every trial's checker.json was written by the
  frozen checker and authorized for new evidence.
- `crashcourt experiment aggregate <batch>...` summarizes verified batches only, into `cells.json`. Batches
  are pooled only when they share this commit, the checker, and the scenario, and their manifests differ in
  no material field a recorded compatibility rule does not cover. Anything else is reported as separate
  groups, with the differing fields named.

## Run order

The instrument runs first. Every `instrument` cell (C1, C3, C2, the misuse controls, and the three published
replications, langgraph#8039, #8796, and #8764) must come out as predicted under this commit, the frozen
checker, and the same environment before any `new-evidence` cell is authorized. A replication that disagrees
with its published outcome opens a harness investigation; it is never a finding.

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

- The checker is frozen: FROZEN.md committed, its hashes copied into `checker`.
- Before the freeze: the landmarks are derived from real fault-free W1 and W5 histories, not from source
  reading; my ruling on the running-at-a-stop definition against the #8039 replication (K1a, A1, E1); my
  confirmation of the checker's documented readings; and the LG-PW split into LG-PW-EXC and LG-PW-CRASH
  (K1b, N4, A2, A3).
- Every scenario file exists, and its hash and master seed are copied into its cell.
- The runner is chosen, and the lockfile, clause registry, and specification hashes are pinned under
  `environment`.
- Each instrument check has its evidence recorded.
- Every `pending` note is resolved, and `state` is set to `final` in my commit.

## Cells

| Cell | Phase | Evidence | Workload | Durability | Fault | Workers | N | Predicted | |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C1-w1-sync | instrument | control (C1) | w1 | sync | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C1-w1-async | instrument | control (C1) | w1 | async | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C1-w1-exit | instrument | control (C1) | w1 | exit | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C1-w1-reject-sync | instrument | control (C1) | w1 | sync | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C1-w1-reject-async | instrument | control (C1) | w1 | async | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C1-w1-misuse-sync | instrument | control (C1) | w1-misuse | sync | - | 1 | 50 | I4: EXPECTED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C1-w1-misuse-async | instrument | control (C1) | w1-misuse | async | - | 1 | 50 | I4: EXPECTED; *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C1-w5-sync | instrument | control (C1) | w5 | sync | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C1-w5-async | instrument | control (C1) | w5 | async | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C3-w1-sync | instrument | control (C3) | w1 | sync | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C3-w1-reject-sync | instrument | control (C3) | w1 | sync | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| C3-w5-sync | instrument | control (C3) | w5 | sync | - | 1 | 50 | *: PASS or NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| K2 | instrument | control (C2) | w5 | sync | F06 | 2 | 20 | I5a: GAP; I6a: NO_ANOMALY_UNSPECIFIED or GAP | pending |
| K5 | instrument | control (misuse) | w1-misuse | sync | - | 1 | 20 | I4: EXPECTED; I7c: NO_ANOMALY_UNSPECIFIED |  |
| K5-reject | instrument | control (misuse) | w1-misuse | sync | - | 1 | 20 | I4: EXPECTED; I7c: NO_ANOMALY_UNSPECIFIED |  |
| K1a | instrument | replication (langgraph#8039) | w1 | sync | F01 | 1 | 20 | I4: EXPECTED; I7a: EXPECTED; I1b: PASS | pending |
| K3 | instrument | replication (langgraph#8796) | w5 | sync | F05 | 2 | 20 | I5a: GAP; I6a: GAP |  |
| K4 | instrument | replication (langgraph#8764) | w1 | sync | F01 | 1 | 20 | I1a: NOT_EXERCISED; I1b: NOT_EXERCISED |  |
| K1b | new-evidence | new-evidence | w1 | sync | F01 | 1 | 20 | I2: NO_ANOMALY_UNSPECIFIED or GAP | pending |
| N1 | new-evidence | new-evidence | w1 | sync | F11 | 1 | 20 | I4: GAP; I7a: GAP | unknown |
| N2 | new-evidence | new-evidence | w1 | sync | F11 | 1 | 20 | I4: PASS or GAP; I11: NO_ANOMALY_UNSPECIFIED | unknown |
| N3 | new-evidence | new-evidence | w1 | sync | F10 | 1 | 20 | I4: EXPECTED |  |
| N4 | new-evidence | new-evidence | w1 | sync | F11 | 1 | 20 | I2: NO_ANOMALY_UNSPECIFIED or GAP | unknown, pending |
| N5 | new-evidence | new-evidence | w1 | sync | F08 | 2 | 20 | I5a: GAP; I7a: GAP; I7c: GAP; I8: GAP |  |
| N6 | new-evidence | new-evidence | w1 | sync | F08 | 2 | 20 | I5a: GAP |  |
| N7 | new-evidence | new-evidence | w1 | sync | F07 | 1 | 20 | I4: GAP | unknown |
| N8 | new-evidence | new-evidence | w1 | sync | F05 | 2 | 20 | I4: GAP; I5a: GAP; I7a: GAP; I6a: GAP |  |
| N9 | new-evidence | new-evidence | w1 | sync | F13 | 1 | 20 | I1b: PASS |  |
| N10 | new-evidence | new-evidence | w1 | sync | F12 | 1 | 20 | I1a: PASS; I1b: PASS; I11: PASS; I4: PASS |  |
| N11 | new-evidence | new-evidence | w1 | sync | F01 | 1 | 20 | I7c: PASS or NO_ANOMALY_UNSPECIFIED | unknown |
| N12 | new-evidence | new-evidence | w5 | sync | F11 | 1 | 20 | I5: NO_ANOMALY_UNSPECIFIED | unknown |
| N12b | new-evidence | new-evidence | w5 | sync | F10 | 1 | 20 | I5b: GAP |  |
| A1 | new-evidence | new-evidence | w1 | async | F01 | 1 | 20 | I4: EXPECTED; I1b: NOT_EXERCISED or EXPECTED | pending |
| A2 | new-evidence | new-evidence | w1 | async | F01 | 1 | 20 | I2: NO_ANOMALY_UNSPECIFIED or GAP | pending |
| A3 | new-evidence | new-evidence | w1 | async | F11 | 1 | 20 | I2: NO_ANOMALY_UNSPECIFIED or GAP; I11: NO_ANOMALY_UNSPECIFIED | unknown, pending |
| A4 | new-evidence | new-evidence | w1 | async | F12 | 1 | 20 | I4: PASS; *: NO_ANOMALY_UNSPECIFIED or NOT_EXERCISED |  |
| E1 | new-evidence | new-evidence | w1 | exit | F01 | 1 | 20 | I4: EXPECTED; I1a: NOT_EXERCISED; I1b: NOT_EXERCISED | pending |

## Deviations

None yet. Any change to this file after it is final is listed here with its reason and the batches it
affects.

## Pre-registration data

```json
{
  "cells": [
    {
      "confidence": "predicted",
      "control": "C1",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-sync",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-async",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "deterministic": true,
      "durability": "exit",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-exit",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-reject-sync",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-reject-async",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-misuse-sync",
      "master_seed": null,
      "n": 50,
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
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w1-misuse-async",
      "master_seed": null,
      "n": 50,
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
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w5-sync",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "scenario_sha256": null,
      "trigger": "one worker, sequential operations, no faults and no arms",
      "workers": 1,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "C1",
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly outside the effect model, or more than 2% INVALID",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C1-w5-async",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly, or a missing fp.hit for an arm",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C3-w1-sync",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "scenario_sha256": null,
      "trigger": "every failpoint the new-evidence cells arm on this workload armed with action none; a latency toxic of 0 ms and 0 jitter on each worker's proxy for the whole trial",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C3",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly, or a missing fp.hit for an arm",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C3-w1-reject-sync",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "trigger": "every failpoint the new-evidence cells arm on this workload armed with action none; a latency toxic of 0 ms and 0 jitter on each worker's proxy for the whole trial",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C3",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly, or a missing fp.hit for an arm",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "C3-w5-sync",
      "master_seed": null,
      "n": 50,
      "pending": null,
      "phase": "instrument",
      "predicted": [
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
      "scenario_sha256": null,
      "trigger": "every failpoint the new-evidence cells arm on this workload armed with action none; a latency toxic of 0 ms and 0 jitter on each worker's proxy for the whole trial",
      "workers": 1,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "C2",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any trial without a fork",
      "evidence": "control",
      "fault": "F06",
      "hypothesis": "H1",
      "id": "K2",
      "master_seed": null,
      "n": 20,
      "pending": "the I6a class in this cell (the methodology states the head, not the class)",
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
          "note": "the head is the larger checkpoint id; LG-LATEST describes, so it never supports PASS"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/K2.yaml",
      "scenario_sha256": null,
      "trigger": "both workers paused at get_tuple.after for their turn ops, released together",
      "workers": 2,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "misuse",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any non-EXPECTED I4",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "K5",
      "master_seed": null,
      "n": 20,
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
          "note": "holds; precheck is exempt by the effect model"
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
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any non-EXPECTED I4, or any charge row",
      "evidence": "control",
      "fault": null,
      "hypothesis": null,
      "id": "K5-reject",
      "master_seed": null,
      "n": 20,
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "(b): still two precheck rows, zero charge"
        },
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED"
          ],
          "invariant": "I7c",
          "note": "holds"
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
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "anything but 20 of 20 as predicted: a harness investigation, never a finding",
      "evidence": "replication",
      "fault": "F01",
      "hypothesis": "H4",
      "id": "K1a",
      "master_seed": null,
      "n": 20,
      "pending": "the founder's ruling on spec 2.0's running-at-a-stop definition against C11-8039; T-10's current reading gives I4 GAP for this shape, not EXPECTED (a)",
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "(a): duplicate charge after a crash before commit"
        },
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I7a",
          "note": null
        },
        {
          "expect": [
            "PASS"
          ],
          "invariant": "I1b",
          "note": null
        }
      ],
      "replication_of": "langgraph#8039",
      "scenario": "scenarios/first-matrix/K1a.yaml",
      "scenario_sha256": null,
      "trigger": "SIGKILL at L-EXW put_writes.before (writes not committed); resume-1 redelivered to the worker's replacement",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": "C11",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any trial without a fork",
      "evidence": "replication",
      "fault": "F05",
      "hypothesis": "H3",
      "id": "K3",
      "master_seed": null,
      "n": 20,
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
          "note": "head decided by clock"
        }
      ],
      "replication_of": "langgraph#8796",
      "scenario": "scenarios/first-matrix/K3.yaml",
      "scenario_sha256": null,
      "trigger": "A SIGSTOP at L-T1 put.before; turn-1 redelivered to B; after B's client.ok, SIGCONT A",
      "workers": 2,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": "C11",
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any row present for the thread; checker.json must record the accepted run with no record",
      "evidence": "replication",
      "fault": "F01",
      "hypothesis": null,
      "id": "K4",
      "master_seed": null,
      "n": 20,
      "pending": null,
      "phase": "instrument",
      "predicted": [
        {
          "expect": [
            "NOT_EXERCISED"
          ],
          "invariant": "I1a",
          "note": null
        },
        {
          "expect": [
            "NOT_EXERCISED"
          ],
          "invariant": "I1b",
          "note": null
        }
      ],
      "replication_of": "langgraph#8764",
      "scenario": "scenarios/first-matrix/K4.yaml",
      "scenario_sha256": null,
      "trigger": "SIGKILL at L-IN put.before, occurrence 1 of the start op",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any re-run",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": "H5",
      "id": "K1b",
      "master_seed": null,
      "n": 20,
      "pending": "the LG-PW split into LG-PW-EXC and LG-PW-CRASH (a T-02 follow-up)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I2",
          "note": "writes reused, or re-run despite committed writes; cites LG-PW-CRASH (describes) after the split"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/K1b.yaml",
      "scenario_sha256": null,
      "trigger": "SIGKILL at L-EXW put_writes.after (writes committed); resume-1 redelivered",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "VIOLATION_CANDIDATE on any invariant",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": "H7",
      "id": "N1",
      "master_seed": null,
      "n": 20,
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
      "trigger": "pause at L-RES put_writes.before; downstream timeout toxic on A's proxy; release; heal after a fixed delay; the client retries resume-1 once as resume-2 after client.fail",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "a duplicate or a fork",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": "H7",
      "id": "N2",
      "master_seed": null,
      "n": 20,
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "PASS",
            "GAP"
          ],
          "invariant": "I4",
          "note": "no duplicate, or a second resume on a non-interrupted thread"
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
      "trigger": "pause at L-EXC put.before; downstream timeout; release; heal after a fixed delay; retry as resume-2",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "not 20 of 20 EXPECTED",
      "evidence": "new-evidence",
      "fault": "F10",
      "hypothesis": null,
      "id": "N3",
      "master_seed": null,
      "n": 20,
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "(a): the writes never committed, so the node re-runs"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N3.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXW put_writes.before; upstream reset_peer; release; retry",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any re-run",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": "H5",
      "id": "N4",
      "master_seed": null,
      "n": 20,
      "pending": "the LG-PW split into LG-PW-EXC and LG-PW-CRASH (a T-02 follow-up)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I2",
          "note": "committed writes reused, or execute re-run"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N4.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXW put_writes.before; downstream timeout; release; heal after a fixed delay; retry",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "UNSTABLE (1 to 19 of 20)",
      "evidence": "new-evidence",
      "fault": "F08",
      "hypothesis": "H2",
      "id": "N5",
      "master_seed": null,
      "n": 20,
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
      "trigger": "A and B paused at get_tuple.after for resume-1 (approve, A) and resume-2 (reject, B); release A; on A's L-RES put_writes.return, release B",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "UNSTABLE",
      "evidence": "new-evidence",
      "fault": "F08",
      "hypothesis": "H2",
      "id": "N6",
      "master_seed": null,
      "n": 20,
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
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "UNSTABLE",
      "evidence": "new-evidence",
      "fault": "F07",
      "hypothesis": null,
      "id": "N7",
      "master_seed": null,
      "n": 20,
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
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "UNSTABLE",
      "evidence": "new-evidence",
      "fault": "F05",
      "hypothesis": "H3",
      "id": "N8",
      "master_seed": null,
      "n": 20,
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
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N8.yaml",
      "scenario_sha256": null,
      "trigger": "A SIGSTOP at L-EXC put.before (charge already fired); resume-1 redelivered to B; after B's client.ok, SIGCONT A",
      "workers": 2,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any acknowledged checkpoint missing",
      "evidence": "new-evidence",
      "fault": "F13",
      "hypothesis": "H8",
      "id": "N9",
      "master_seed": null,
      "n": 20,
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "PASS"
          ],
          "invariant": "I1b",
          "note": "every earlier acknowledged checkpoint survives"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N9.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXC put.before; database restart; release; the put fails; retry as resume-2",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any anomaly (VIOLATION_CANDIDATE by the F12 rule)",
      "evidence": "new-evidence",
      "fault": "F12",
      "hypothesis": null,
      "id": "N10",
      "master_seed": null,
      "n": 20,
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
            "PASS"
          ],
          "invariant": "I4",
          "note": null
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/N10.yaml",
      "scenario_sha256": null,
      "trigger": "pause at L-EXC put.before; downstream latency 24 h; release; heal after a fixed delay",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "any charge row",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": null,
      "id": "N11",
      "master_seed": null,
      "n": 20,
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
      "trigger": "W1 with the resume rejected; SIGKILL at L-RES put_writes.after (resume committed); redeliver",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "a dangling parent or a fork",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": null,
      "id": "N12",
      "master_seed": null,
      "n": 20,
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
      "trigger": "pause at L-T1 put.before; downstream timeout; release; heal; turn-2 to the same worker",
      "workers": 1,
      "workload": "w5"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "sync",
      "escalate_if": "n/a: recorded",
      "evidence": "new-evidence",
      "fault": "F10",
      "hypothesis": null,
      "id": "N12b",
      "master_seed": null,
      "n": 20,
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
      "deterministic": true,
      "durability": "async",
      "escalate_if": "not as predicted",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": "H4",
      "id": "A1",
      "master_seed": null,
      "n": 20,
      "pending": "the founder's ruling on spec 2.0's running-at-a-stop definition against C11-8039; T-10's current reading gives I4 GAP for this shape, not EXPECTED (a)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "(a), unchanged"
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
      "trigger": "K1a under async",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any re-run",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": null,
      "id": "A2",
      "master_seed": null,
      "n": 20,
      "pending": "the LG-PW split into LG-PW-EXC and LG-PW-CRASH (a T-02 follow-up)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I2",
          "note": "as K1b, classified with LG-PW-CRASH"
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/A2.yaml",
      "scenario_sha256": null,
      "trigger": "K1b under async",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "unknown",
      "control": null,
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any re-run",
      "evidence": "new-evidence",
      "fault": "F11",
      "hypothesis": null,
      "id": "A3",
      "master_seed": null,
      "n": 20,
      "pending": "the LG-PW split into LG-PW-EXC and LG-PW-CRASH (a T-02 follow-up)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "NO_ANOMALY_UNSPECIFIED",
            "GAP"
          ],
          "invariant": "I2",
          "note": "as N4"
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
      "trigger": "N4 under async",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "async",
      "escalate_if": "any anomaly",
      "evidence": "new-evidence",
      "fault": "F12",
      "hypothesis": null,
      "id": "A4",
      "master_seed": null,
      "n": 20,
      "pending": null,
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "PASS"
          ],
          "invariant": "I4",
          "note": null
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
      "trigger": "N10 under async",
      "workers": 1,
      "workload": "w1"
    },
    {
      "confidence": "predicted",
      "control": null,
      "deterministic": true,
      "durability": "exit",
      "escalate_if": "not as predicted",
      "evidence": "new-evidence",
      "fault": "F01",
      "hypothesis": null,
      "id": "E1",
      "master_seed": null,
      "n": 20,
      "pending": "the founder's ruling on spec 2.0's running-at-a-stop definition against C11-8039; T-10's current reading gives I4 GAP for this shape, not EXPECTED (a)",
      "phase": "new-evidence",
      "predicted": [
        {
          "expect": [
            "EXPECTED"
          ],
          "invariant": "I4",
          "note": "under LG-DUR-EXIT and LG-INT-RERUN: the resume re-runs from the interrupt"
        },
        {
          "expect": [
            "NOT_EXERCISED"
          ],
          "invariant": "I1a",
          "note": null
        },
        {
          "expect": [
            "NOT_EXERCISED"
          ],
          "invariant": "I1b",
          "note": null
        }
      ],
      "replication_of": null,
      "scenario": "scenarios/first-matrix/E1.yaml",
      "scenario_sha256": null,
      "trigger": "K1a under exit",
      "workers": 1,
      "workload": "w1"
    }
  ],
  "checker": null,
  "environment": {
    "clause_registry_sha256": null,
    "git_dirty": false,
    "lockfile_sha256": null,
    "os_system": "Linux",
    "postgres.fsync": "on",
    "postgres.synchronous_commit": "on",
    "postgres.track_commit_timestamp": "on",
    "runner": null,
    "spec_sha256": null
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
      "requirement": "saver mutants M1-M5 each caught by its target invariant in at least 19 of 20 trials (T-11)"
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
      "requirement": "sequential reference: InMemorySaver final state equals Postgres for single-writer fault-free runs"
    }
  ],
  "max_invalid_share": 0.02,
  "name": "first-matrix",
  "seeds": {
    "derivation": "sha256(json([master_seed, cell, trial]))",
    "master_seed_in": "scenario"
  },
  "state": "draft",
  "v": 1
}
```
