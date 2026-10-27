# V1 build records — historical archive

These files were moved **byte-for-byte** from the original `tasks/` directory after V1 closed.
They are a record of implementation decisions, reviews, experiments, and the development
process—not the project's current issue tracker, release status, or an instruction to rerun a
published experiment. Statements such as “PR open,” “pending review,” “to do,” and deferred
learning exercises reflect the date each record was written and have **not** been rewritten to
make the project's history look more polished.

## What these records are for

| Records | Historical value |
| --- | --- |
| `T-00`, `T-01`, `T-02` | Prior-art/name decision, dependency and environment pins, clause registry origins |
| `T-03`, `T-04`, `T-05`, `T-06` | Source-level saver audit, protocol and history architecture, fault-injection decisions |
| `T-07`, `T-08`, `T-09` | End-to-end runner, independent sink/ledger, controlled workloads |
| `T-10`, `T-11`, `T-13` | Frozen-checker design, instrument validation, pre-registration and statistical decisions |
| `F-01`, `F-02` | Evidence-explorer decisions, demo provenance, release-fail-closed guards |
| `RECORD_TEMPLATE` | Superseded administrative template, retained only for historical fidelity |

Some entries include extensive PR logistics, superseded plans, and unfinished personal
learning checkpoints. They are **historical process artifacts**, not evidence that V1
remains unfinished. Their presence is transparent but should not be confused with verified
experimental results.

## Authoritative public evidence

- [Engineering overview](../../ENGINEERING_CASE_STUDY.md) — concise implementation and impact
- [Reproduction guide](../../REPRODUCING.md) — independently executed from a fresh clone in the V1 closure audit
- [Source audit](../../../research/source-audit.md) — actual upstream code behavior and documented guarantees
- [Pre-registration](../../../research/preregistration.md) — binding predictions and recorded deviations
- [Frozen checker](../../../src/crashcourt/checker/FROZEN.md) — fixed evidence predicates
- [N8 technical report](../../../research/n8-report.md) and [operations record](../../../research/n8-mitigation-operations.md) — all results, including stopped runs and I6a mismatches
- [V1 checksummed evidence release](https://github.com/s2002kumar/crashcourt/releases/tag/evidence-2026-10) — published raw-run artifacts and provenance

The git history, merged PRs, and registered source commits remain intact. No record has
been edited to conceal work performed with AI assistance; AI-assisted implementation and
review should be evaluated against the tests and public evidence, not treated as proof by itself.
