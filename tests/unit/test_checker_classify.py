"""Layer B: clause polarity from the registry, grades from the mapping, witnesses for EXPECTED,
the requirements of VIOLATION_CANDIDATE, and the properties that must hold for any input."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from crashcourt.checker.classify import Scope, classify
from crashcourt.checker.mapping import CARRYING, RULES
from crashcourt.checker.observations import (
    DISCLAIMED,
    HARNESS_EXPLAINED,
    STOP_WITNESSED,
    WRITER_CRASHED,
    Anomaly,
    Observation,
    Status,
    Witness,
)
from crashcourt.clauses.models import Clause, Polarity
from crashcourt.clauses.registry import ClauseRegistry
from tests.mutants import artifacts as M
from tests.unit import _trials as T
from tests.unit.test_checker_engine import NAU, outcomes, run

REGISTRY, _ = T.registry()
SCOPE = Scope(target="AsyncPostgresSaver", durability="sync", packages=T.PACKAGES)
ALL_WITNESSES = sorted(
    {w for r in RULES.values() for p in r.permits for w in p.requires}
    | {w for r in RULES.values() if r.guarantee for w in r.guarantee.requires}
    | {DISCLAIMED, HARNESS_EXPLAINED, STOP_WITNESSED}
)


def registry_with(**polarities: Polarity) -> ClauseRegistry:
    return ClauseRegistry(
        c.model_copy(update={"polarity": polarities[c.id]}) if c.id in polarities else c
        for c in REGISTRY
    )


def anomaly(*held: str, case: str | None = None) -> Anomaly:
    return Anomaly(
        evidence=("history/w.jsonl#seq=1",),
        sub_reason="x",
        case=case,
        witnesses=tuple(Witness(name=w, held=True, evidence=(f"w#{w}",)) for w in held),
    )


def obs(
    invariant: str, *anomalies: Anomaly, case: str | None = None, disclaimed: bool = False
) -> Observation:
    return Observation(
        invariant=invariant,
        status="ANOMALY" if anomalies else "NO_ANOMALY",
        anomalies=anomalies,
        case=case,
        witnesses=(Witness(name=DISCLAIMED, held=disclaimed),),
    )


def outcome(
    o: Observation, registry: ClauseRegistry = REGISTRY, scope: Scope = SCOPE
) -> tuple[str, str | None, str]:
    c = classify(o, RULES[o.invariant], registry, scope)
    assert c is not None
    return c.result.outcome, c.result.clause, str(c.result.reason)


# -- the normative mapping ----------------------------------------------------------------------


def test_guarantee_carries_pass_and_candidate_with_its_grade() -> None:
    assert outcome(obs("I1a"))[:2] == ("PASS", "LG-DUR-SYNC")
    result, clause, reason = outcome(obs("I1b", anomaly()))
    assert (result, clause) == ("VIOLATION_CANDIDATE", "LG-DUR-SYNC")
    assert "G2 caveat" in reason


def test_describes_never_carries_pass_or_a_candidate() -> None:
    """Founder decision 1: a describes clause supports no PASS on its own, whatever the table."""
    flipped = registry_with(**{"LG-DUR-SYNC": Polarity.DESCRIBES})
    assert outcome(obs("I1a"), flipped)[:2] == NAU
    assert outcome(obs("I1a", anomaly()), flipped)[:2] == ("GAP", None)
    for name in ("I5a", "I5b", "I5c", "I6a", "I6b"):
        assert outcome(obs(name))[:2] == NAU
        assert outcome(obs(name, anomaly()))[:2] == ("GAP", None)


def test_a_clause_out_of_scope_does_not_apply() -> None:
    old = Scope(
        target="AsyncPostgresSaver",
        durability="sync",
        packages={**T.PACKAGES, "langgraph": "1.3.0"},
    )
    other = Scope(target="SomeOtherSaver", durability="sync", packages=T.PACKAGES)
    asynchronous = Scope(target="AsyncPostgresSaver", durability="async", packages=T.PACKAGES)
    for scope in (old, other, asynchronous):
        assert outcome(obs("I1b"), scope=scope)[:2] == NAU
        assert outcome(obs("I1b", anomaly()), scope=scope)[:2] == ("GAP", None)


def test_permit_needs_every_condition_witnessed() -> None:
    asynchronous = Scope(target="AsyncPostgresSaver", durability="async", packages=T.PACKAGES)
    assert outcome(obs("I1b", anomaly(WRITER_CRASHED)), scope=asynchronous)[:2] == (
        "EXPECTED",
        "LG-DUR-ASYNC",
    )
    # "async may lose checkpoints if the process crashes" does not cover a loss with no crash.
    assert outcome(obs("I1b", anomaly()), scope=asynchronous)[:2] == ("GAP", None)
    # A witnessed crash under sync is no permission either.
    assert outcome(obs("I1b", anomaly(WRITER_CRASHED)))[:2] == (
        "VIOLATION_CANDIDATE",
        "LG-DUR-SYNC",
    )


def test_i4_cause_order() -> None:
    rule_a = ("stop_while_running", "resume_after_stop", "single_writer")
    rule_b = ("before_interrupt_in_model", "interrupt_witnessed", "resume_after_interrupt")
    assert outcome(obs("I4", anomaly(*rule_a)))[:2] == ("EXPECTED", "LG-DE-RERUN")
    assert outcome(obs("I4", anomaly(*rule_b)))[:2] == ("EXPECTED", "LG-INT-RERUN")
    # (a) without the sequential writer set falls through to (c).
    assert outcome(obs("I4", anomaly(*rule_a[:2], DISCLAIMED)))[:2] == ("GAP", None)
    # (d) in the exception case: LG-PW, G3.
    assert outcome(obs("I4", anomaly("committed_before_stop", case="exception")))[:2] == (
        "VIOLATION_CANDIDATE",
        "LG-PW",
    )
    # (d) in the crash case: LG-PW does not cover a crash; with no LG-PW-CRASH in the registry, GAP.
    result, clause, reason = outcome(obs("I4", anomaly("committed_before_stop", case="crash")))
    assert (result, clause) == ("GAP", None) and "crash case" in reason
    # (e) nothing: GAP, and with no stop witnessed a harness investigation.
    c = classify(obs("I4", anomaly()), RULES["I4"], REGISTRY, SCOPE)
    assert c is not None and c.result.outcome == "GAP" and c.investigations


def test_lg_pw_split_is_followed_when_the_registry_has_it() -> None:
    """Founder decision 2: after the split, the crash case cites LG-PW-CRASH (describes) and the
    exception case LG-PW-EXC (guarantees)."""
    pw = REGISTRY.get("LG-PW")
    split = ClauseRegistry(
        [
            *REGISTRY,
            pw.model_copy(update={"id": "LG-PW-EXC"}),
            pw.model_copy(update={"id": "LG-PW-CRASH", "polarity": Polarity.DESCRIBES}),
        ]
    )
    assert outcome(obs("I2", case="exception"), split)[:2] == ("PASS", "LG-PW-EXC")
    assert outcome(obs("I2", anomaly(case="exception")), split)[:2] == (
        "VIOLATION_CANDIDATE",
        "LG-PW-EXC",
    )
    assert outcome(obs("I2", case="crash"), split)[:2] == NAU
    assert outcome(obs("I2", anomaly(case="crash")), split)[:2] == ("GAP", None)


def test_candidate_requirements_each_withhold_it() -> None:
    assert outcome(obs("I1b", anomaly()))[0] == "VIOLATION_CANDIDATE"
    # Disclaimed scenario: GAP.
    assert outcome(obs("I1b", anomaly(DISCLAIMED)))[:2] == ("GAP", None)
    # A harness-originated event on the thread: withheld, and said so.
    result, _, reason = outcome(obs("I1b", anomaly(HARNESS_EXPLAINED)))
    assert result == "GAP" and "withheld" in reason
    # Fault verification not passed: withheld.
    unverified = Scope(
        target="AsyncPostgresSaver", durability="sync", packages=T.PACKAGES, faults_verified=False
    )
    assert outcome(obs("I1b", anomaly()), scope=unverified)[0] == "GAP"
    # A guaranteeing clause marked otherwise in the registry: no candidate.
    assert (
        outcome(obs("I1b", anomaly()), registry_with(**{"LG-DUR-SYNC": Polarity.PERMITS}))[0]
        == "GAP"
    )


def test_disclaimed_scenario_has_no_pass() -> None:
    assert outcome(obs("I1b", disclaimed=True))[:2] == NAU


def test_strongest_outcome_wins_across_anomalies() -> None:
    expected = anomaly("stop_while_running", "resume_after_stop", "single_writer")
    gap = anomaly(STOP_WITNESSED)
    candidate = anomaly("committed_before_stop", case="exception")
    assert outcome(obs("I4", expected, gap))[0] == "GAP"
    assert outcome(obs("I4", expected, gap, candidate))[:2] == ("VIOLATION_CANDIDATE", "LG-PW")
    assert outcome(obs("I4", expected, expected))[0] == "EXPECTED"


def test_not_exercised_produces_nothing() -> None:
    o = Observation(invariant="I1a", status="NOT_EXERCISED", missing="no pair")
    assert classify(o, RULES["I1a"], REGISTRY, SCOPE) is None


# -- end to end: the classifier reads the registry ---------------------------------------------


def test_async_crash_end_to_end() -> None:
    """A missing acknowledged checkpoint under async: EXPECTED with the writer's crash witnessed,
    GAP without it."""
    lost = M.am1_delete_head(T.w1_trial(durability="async"))
    assert outcomes(run(lost))["I1b"] == ("GAP", None)

    crashed = T.with_declared_kill(lost)
    assert outcomes(run(crashed))["I1b"] == ("EXPECTED", "LG-DUR-ASYNC")


# -- properties --------------------------------------------------------------------------------

POLARITIES = list(Polarity)


@st.composite
def cases(draw: st.DrawFn) -> tuple[Observation, ClauseRegistry, Scope]:
    invariant = draw(st.sampled_from(sorted(RULES)))
    status: Status = draw(st.sampled_from(["ANOMALY", "NO_ANOMALY"]))
    case = draw(st.sampled_from([None, "exception", "crash"]))
    witness_sets = draw(st.lists(st.sets(st.sampled_from(ALL_WITNESSES)), min_size=1, max_size=3))
    anomalies = (
        tuple(anomaly(*sorted(ws), case=case) for ws in witness_sets) if status == "ANOMALY" else ()
    )
    o = Observation(
        invariant=invariant,
        status=status,
        anomalies=anomalies,
        case=None if anomalies else case,
        witnesses=(Witness(name=DISCLAIMED, held=draw(st.booleans())),),
    )
    flips = draw(
        st.dictionaries(st.sampled_from([c.id for c in REGISTRY]), st.sampled_from(POLARITIES))
    )
    extra = draw(st.sets(st.sampled_from(["LG-PW-EXC", "LG-PW-CRASH"])))
    clauses: list[Clause] = [
        c.model_copy(update={"polarity": flips.get(c.id, c.polarity)}) for c in REGISTRY
    ]
    clauses += [
        REGISTRY.get("LG-PW").model_copy(
            update={"id": e, "polarity": draw(st.sampled_from(POLARITIES))}
        )
        for e in sorted(extra)
    ]
    scope = Scope(
        target=draw(st.sampled_from(["AsyncPostgresSaver", "SqliteSaver", "OtherSaver"])),
        durability=draw(st.sampled_from(["sync", "async", "exit"])),
        packages={**T.PACKAGES, "langgraph": draw(st.sampled_from(["1.2.12", "1.3.0"]))},
        faults_verified=draw(st.booleans()),
    )
    return o, ClauseRegistry(clauses), scope


@settings(max_examples=400, deadline=None)
@given(cases())
def test_classification_properties(case: tuple[Observation, ClauseRegistry, Scope]) -> None:
    o, registry, scope = case
    classified = classify(o, RULES[o.invariant], registry, scope)
    assert classified is not None
    result = classified.result
    rules = RULES[o.invariant]
    # The checker never writes VIOLATION or INVALID from Layer B.
    assert result.outcome in {
        "PASS",
        "NO_ANOMALY_UNSPECIFIED",
        "EXPECTED",
        "GAP",
        "VIOLATION_CANDIDATE",
    }
    if result.clause is not None:
        clause = registry.get(result.clause)
        # A describes, disclaims, or proposed clause never carries an outcome.
        assert clause.polarity in {Polarity.GUARANTEES, Polarity.PERMITS}
        assert scope.target in clause.applies_to
        assert clause.version_scope.contains(scope.packages[clause.version_scope.package])
    if result.outcome in {"PASS", "VIOLATION_CANDIDATE"}:
        clause = registry.get(str(result.clause))
        assert clause.polarity is Polarity.GUARANTEES
        assert rules.guarantee is not None
        uses = [u for us in rules.guarantee.cases.values() for u in us if u.clause == clause.id]
        assert uses and all(u.grade in CARRYING for u in uses)
        assert all(u.durability in (None, scope.durability) for u in uses)
        if any(u.grade == "G2" for u in uses):
            assert "G2 caveat" in str(result.reason)
    if result.outcome == "PASS":
        assert o.status == "NO_ANOMALY" and not o.held(DISCLAIMED)
    if result.outcome == "VIOLATION_CANDIDATE":
        assert scope.faults_verified
        assert rules.guarantee is not None
        assert any(
            not a.held(DISCLAIMED)
            and not a.held(HARNESS_EXPLAINED)
            and all(a.held(w) for w in rules.guarantee.requires)
            for a in o.anomalies
        )
    if result.outcome == "EXPECTED":
        clause = registry.get(str(result.clause))
        assert clause.polarity is Polarity.PERMITS
        permits = [p for p in rules.permits if p.use.clause == clause.id]
        assert any(all(a.held(w) for w in p.requires) for p in permits for a in o.anomalies)
    if o.status == "NO_ANOMALY":
        assert result.outcome in {"PASS", "NO_ANOMALY_UNSPECIFIED"}
    else:
        assert result.outcome in {"EXPECTED", "GAP", "VIOLATION_CANDIDATE"}
        assert result.evidence


def test_reviewed_values_never_come_from_the_checker() -> None:
    """VIOLATION needs a repro and a review; the checker sets neither."""
    for artifacts in (
        T.w1_trial(),
        M.am1_delete_head(T.w1_trial()),
        T.exception_case_trial(rerun=True),
    ):
        out = run(artifacts)
        assert out.result is not None
        for r in out.result.results:
            assert r.outcome != "VIOLATION"
            assert r.repro is None and r.review is None
