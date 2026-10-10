"""Layer B: from an observation, the outcome and the clause it rests on.

Layer B reads only Layer A's observations (anomalies, evidence pointers, named witnesses), the
invariant's rules in crashcourt.checker.mapping, the clause registry, and the scenario's scope.
It never reads raw records. The order, for each anomaly (red-team specification 1.1):

1. EXPECTED, if a permitting clause applies and every condition it states is witnessed. A
   permission with an unwitnessed condition does not apply: the anomaly falls through.
2. GAP, if the scenario is one the LGP-DT disclaimer covers (more than one writer or more than
   one resume on the thread).
3. VIOLATION_CANDIDATE, if a guaranteeing clause applies (polarity `guarantees` in the registry,
   grade G3 or G2, target, version, and durability in scope) and forbids the anomaly, the fault
   verification passed, and no harness-originated event on the thread may explain the anomaly.
4. GAP otherwise. When a guaranteeing clause applies but a harness-originated event may explain
   the anomaly, the GAP says the candidate was withheld and why.

No anomaly is PASS only under a carrying guaranteeing clause in a scenario that is not
disclaimed; otherwise NO_ANOMALY_UNSPECIFIED. NOT_EXERCISED produces no outcome at all. The
checker never writes VIOLATION. Where an invariant has several anomalies in one trial, the result
is the strongest: VIOLATION_CANDIDATE over GAP over EXPECTED, citing the first anomaly that has it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from crashcourt.checker.artifacts import Durability
from crashcourt.checker.mapping import CARRYING, ClauseUse, InvariantRules
from crashcourt.checker.observations import DISCLAIMED, HARNESS_EXPLAINED, Anomaly, Observation
from crashcourt.clauses.models import Clause, Polarity
from crashcourt.clauses.registry import ClauseRegistry
from crashcourt.spec.records import InvariantResult, Outcome

_RANK: dict[str, int] = {"EXPECTED": 0, "GAP": 1, "VIOLATION_CANDIDATE": 2}


@dataclass(frozen=True)
class Scope:
    """What decides whether a clause applies to the trial."""

    target: str
    durability: Durability
    #: Installed distribution versions, from the trial's manifest.
    packages: Mapping[str, str]
    #: Pointers to the fault-fired witnesses (3.6); VIOLATION_CANDIDATE cites them.
    fault_evidence: tuple[str, ...] = ()
    #: Whether every fault-fired check passed. A trial where one failed is INVALID before Layer B
    #: runs, so this is always true here; Layer B still refuses a candidate without it.
    faults_verified: bool = True


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    clause: str | None
    evidence: tuple[str, ...]
    reason: str
    investigate: bool = False


@dataclass(frozen=True)
class Classified:
    result: InvariantResult
    #: Reasons for a harness investigation this outcome raises.
    investigations: tuple[str, ...]


def applicable(
    registry: ClauseRegistry, use: ClauseUse, scope: Scope, polarity: Polarity
) -> Clause | None:
    """The clause, if the registry holds it with this polarity and it applies to the scope."""
    try:
        clause = registry.get(use.clause)
    except KeyError:
        return None
    if clause.polarity is not polarity or scope.target not in clause.applies_to:
        return None
    version = scope.packages.get(clause.version_scope.package)
    if version is None or not clause.version_scope.contains(version):
        return None
    if use.durability is not None and use.durability != scope.durability:
        return None
    return clause


def _registered(registry: ClauseRegistry, uses: tuple[ClauseUse, ...]) -> ClauseUse | None:
    """The first candidate the registry holds (at any polarity)."""
    for use in uses:
        try:
            registry.get(use.clause)
        except KeyError:
            continue
        return use
    return None


def _guarantee(
    registry: ClauseRegistry, rules: InvariantRules, case: str | None, scope: Scope
) -> tuple[ClauseUse | None, Clause | None]:
    """The case's candidate clause, and the clause itself if it can carry PASS or a candidate."""
    if rules.guarantee is None:
        return None, None
    use = _registered(registry, rules.guarantee.cases.get(case, ()))
    if use is None:
        return None, None
    clause = applicable(registry, use, scope, Polarity.GUARANTEES)
    if clause is None or use.grade not in CARRYING:
        return use, None
    return use, clause


def _notes(use: ClauseUse) -> str:
    parts = [p for p in (use.caveat, use.reading) if p]
    return f"; {'; '.join(parts)}" if parts else ""


def _evidence(anomaly: Anomaly, *names: str) -> tuple[str, ...]:
    items = list(anomaly.evidence)
    for name in names:
        witness = anomaly.witness(name)
        if witness is not None:
            items.extend(witness.evidence)
    return tuple(dict.fromkeys(items))


def classify_anomaly(
    anomaly: Anomaly, rules: InvariantRules, registry: ClauseRegistry, scope: Scope
) -> Decision:
    disclaimed = Decision(
        "GAP",
        None,
        _evidence(anomaly, DISCLAIMED),
        f"{anomaly.sub_reason}; more than one writer or more than one resume on the thread, "
        "which LGP-DT disclaims",
    )
    if rules.disclaim_first and anomaly.held(DISCLAIMED):
        return disclaimed
    for permit in rules.permits:
        clause = applicable(registry, permit.use, scope, Polarity.PERMITS)
        if clause is not None and all(anomaly.held(w) for w in permit.requires):
            return Decision(
                "EXPECTED",
                clause.id,
                _evidence(anomaly, *permit.requires),
                f"{anomaly.sub_reason}; {permit.label}; every condition of {clause.id} witnessed",
            )
    if anomaly.held(DISCLAIMED):
        return disclaimed
    use, clause = _guarantee(registry, rules, anomaly.case, scope)
    forbids = rules.guarantee is not None and all(anomaly.held(w) for w in rules.guarantee.requires)
    if clause is not None and use is not None and forbids:
        if anomaly.held(HARNESS_EXPLAINED) or not scope.faults_verified:
            return Decision(
                "GAP",
                None,
                _evidence(anomaly, HARNESS_EXPLAINED),
                f"{anomaly.sub_reason}; VIOLATION_CANDIDATE under {clause.id} withheld: a "
                "harness-originated event on the thread may explain the anomaly",
            )
        requires = rules.guarantee.requires if rules.guarantee is not None else ()
        return Decision(
            "VIOLATION_CANDIDATE",
            clause.id,
            tuple(dict.fromkeys((*_evidence(anomaly, *requires), *scope.fault_evidence))),
            f"{anomaly.sub_reason}; forbidden by {clause.id} ({use.grade}){_notes(use)}",
        )
    reason = f"{anomaly.sub_reason}; {rules.fallback}"
    if rules.guarantee is not None and rules.guarantee.requires and forbids:
        case = f"the {anomaly.case} case" if anomaly.case else "this case"
        carrier = f"{use.clause} does not carry a verdict" if use is not None else "no clause"
        reason += f" ({', '.join(rules.guarantee.requires)} witnessed; for {case}, {carrier})"
    elif use is not None and forbids:
        reason += f" ({use.clause} is not a carrying guarantee for this scenario)"
    investigate = rules.investigate and (
        rules.investigate_unless is None or not anomaly.held(rules.investigate_unless)
    )
    if investigate:
        reason += "; harness investigation"
    return Decision("GAP", None, anomaly.evidence, reason, investigate)


def classify(
    observation: Observation, rules: InvariantRules, registry: ClauseRegistry, scope: Scope
) -> Classified | None:
    """The invariant's outcome, or None when it was not exercised (it is omitted)."""
    name = observation.invariant
    if observation.status == "NOT_EXERCISED":
        return None
    if observation.status == "NO_ANOMALY":
        use, clause = _guarantee(registry, rules, observation.case, scope)
        if clause is not None and use is not None and not observation.held(DISCLAIMED):
            result = InvariantResult(
                invariant=name,
                outcome="PASS",
                clause=clause.id,
                reason=f"no anomaly under {clause.id} ({use.grade}){_notes(use)}",
            )
        else:
            why = (
                "more than one writer or more than one resume on the thread (LGP-DT)"
                if observation.held(DISCLAIMED) and clause is not None
                else "no carrying guaranteeing clause applies"
            )
            result = InvariantResult(
                invariant=name, outcome="NO_ANOMALY_UNSPECIFIED", reason=f"no anomaly; {why}"
            )
        return Classified(result, ())
    decisions = [classify_anomaly(a, rules, registry, scope) for a in observation.anomalies]
    top = max(_RANK[d.outcome] for d in decisions)
    chosen = [d for d in decisions if _RANK[d.outcome] == top]
    first = chosen[0]
    evidence = tuple(dict.fromkeys(item for d in chosen for item in d.evidence))
    reason = first.reason
    if len(decisions) > 1:
        others = sorted(
            {f"{d.outcome} ({d.clause or 'no clause'})" for d in decisions}
            - {f"{first.outcome} ({first.clause or 'no clause'})"}
        )
        reason += f"; {len(decisions)} anomalies" + (
            f"; also {', '.join(others)}" if others else ""
        )
    result = InvariantResult(
        invariant=name,
        outcome=first.outcome,
        clause=first.clause,
        evidence=evidence,
        reason=reason,
    )
    investigations = tuple(f"{name}: {d.reason}" for d in decisions if d.investigate)
    return Classified(result, investigations)
