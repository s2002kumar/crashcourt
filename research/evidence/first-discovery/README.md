# First discovery tranche: the reported instrument checks

The two controls reports the gate reads before N8 (research/preregistration.md, `instrument_checks`),
produced by `.github/workflows/controls.yml` on the canonical runner, GitHub-hosted Ubuntu 24.04, at
commit 871f773, the tranche's declaration. Each names the commit it ran from, the frozen checker, the
manifest, and the sha256 of every file it ran; the gate compares those with the pre-registration commit.

| File | Check | Actions run | Result |
| --- | --- | --- | --- |
| c5.json | C5, saver mutants (tests/mutants/c5.py) | 39035489063 | M3 20 of 20, M4 20 of 20, M5 20 of 20; M1 and M2 NOT EXERCISED |
| c9.json | C9, sequential reference (tests/controls/c9.py) | 39035500894 | C1-w1-sync 50 of 50 equal; C1-w5-sync 50 of 50 equal |

Neither is a result about LangGraph. The batches themselves are in the runs' artifacts.
