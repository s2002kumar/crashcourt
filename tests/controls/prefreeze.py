"""The pre-freeze validation on real fault-free histories, run once T-09 has produced them.

For a batch of fault-free trials of one workload (control C1's shape), it reports:

1. Landmarks: each landmark of the table located in every trial, at the same occurrence
   (crashcourt.checker.landmarks). These numbers go into the scenarios' arms after the audit.
2. No false anomaly: every trial classifies with no INVALID beyond the 2% the specification
   allows, and no outcome other than PASS or NO_ANOMALY_UNSPECIFIED, except the duplicate the
   misuse workload's model documents (I4 EXPECTED under LG-INT-RERUN).
3. Mutants: AM1-AM6, AM8, and AM10 applied to a copy of one trial flip exactly the outcome the
   specification names (AM7 and AM9 need a fault trial; they are covered by the synthetic tests).

Nothing here changes a predicate. A failure is a question for the audit: a wrong landmark rule, a
wrong effect model, or a harness problem, never a reason to retune the checker on new evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from crashcourt.checker.artifacts import LandmarkTable, TrialArtifacts
from crashcourt.checker.engine import CheckerOutput, check_trial
from crashcourt.checker.landmarks import locate, occurrences
from crashcourt.clauses.registry import ClauseRegistry
from crashcourt.history.merge import group_by_process
from tests.mutants import artifacts as M

INVALID_LIMIT = 0.02
QUIET = frozenset({"PASS", "NO_ANOMALY_UNSPECIFIED"})

Expectation = tuple[str, str, str | None]  # (invariant, outcome, clause)

#: What each mutant must produce on a fault-free W1 approve trial (red-team specification 3.4,
#: with founder decision 4 for AM5).
W1_MUTANTS: dict[str, tuple[Callable[[TrialArtifacts], TrialArtifacts], Expectation | int]] = {
    "AM1": (M.am1_delete_head, ("I1b", "VIOLATION_CANDIDATE", "LG-DUR-SYNC")),
    "AM2": (M.am2_delete_middle, ("I5b", "GAP", None)),
    "AM3": (M.am3_second_child, ("I5a", "GAP", None)),
    "AM4": (M.am4_delete_blob, ("I5c", "GAP", None)),
    "AM5": (M.am5_delete_effect, 15),
    "AM6": (M.am6_duplicate_effect, ("I4", "GAP", None)),
    "AM8": (M.am8_start_before_put_return, ("I1a", "VIOLATION_CANDIDATE", "LG-DUR-SYNC")),
    "AM10": (M.am10_reorder, 2),
}


@dataclass
class Validation:
    trials: int = 0
    landmarks: dict[tuple[str, str, str], int] = field(
        default_factory=dict[tuple[str, str, str], int]
    )
    problems: list[str] = field(default_factory=list[str])

    @property
    def passed(self) -> bool:
        return not self.problems


def _outcomes(out: CheckerOutput) -> dict[str, tuple[str, str | None]]:
    return {r.invariant: (r.outcome, r.clause) for r in (out.result.results if out.result else ())}


def _documented(workload: str, invariant: str, outcome: str, clause: str | None) -> bool:
    return workload == "w1-misuse" and (invariant, outcome, clause) == (
        "I4",
        "EXPECTED",
        "LG-INT-RERUN",
    )


def validate(
    trials: Sequence[TrialArtifacts],
    table: LandmarkTable,
    registry: ClauseRegistry,
    registry_sha256: str,
    *,
    mutate: TrialArtifacts | None = None,
) -> Validation:
    report = Validation(trials=len(trials))
    found = []
    invalid = 0
    for artifacts in trials:
        sites, problems = locate(table, group_by_process(h.events for h in artifacts.histories))
        found.append(sites)
        name = f"trial {artifacts.scenario.trial}"
        report.problems.extend(f"{name}: landmark {p}" for p in problems)
        out = check_trial(artifacts, registry, registry_sha256=registry_sha256)
        if out.report.invalid is not None:
            invalid += 1
            report.problems.append(f"{name}: INVALID {out.report.invalid.reason}")
            continue
        for invariant, (outcome, clause) in _outcomes(out).items():
            if outcome not in QUIET and not _documented(
                artifacts.scenario.workload, invariant, outcome, clause
            ):
                report.problems.append(f"{name}: false anomaly {invariant} {outcome} ({clause})")
    if trials and invalid / len(trials) > INVALID_LIMIT:
        report.problems.append(f"{invalid} of {len(trials)} trials INVALID, above 2%")
    report.landmarks, stability = occurrences(found)
    report.problems.extend(f"landmark {p}" for p in stability)
    if mutate is not None:
        for label, (mutant, expected) in W1_MUTANTS.items():
            out = check_trial(mutant(mutate), registry, registry_sha256=registry_sha256)
            if isinstance(expected, int):
                got = out.report.invalid.trigger if out.report.invalid else None
                if got != expected:
                    report.problems.append(f"{label}: trigger {got}, expected {expected}")
                continue
            invariant, outcome, clause = expected
            if _outcomes(out).get(invariant) != (outcome, clause):
                report.problems.append(
                    f"{label}: {invariant} is {_outcomes(out).get(invariant)}, expected "
                    f"{(outcome, clause)}"
                )
    return report
