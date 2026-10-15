"""The controls the gate reads before new evidence: C5's report, and the C3, K1a, and K3 instrument
cells. Every failed, missing, or mismatched control must refuse a new-evidence batch, against a
real git repository.

C5 is the first matrix's revised methodology (crashcourt.experiment.controls.C5_METHOD): M3 and M4
on W1 approve, M5 on W5 sequential, each caught by its intended invariant in at least 19 of 20;
M1 and M2 NOT EXERCISED, recorded beside the results and never counted as passing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from crashcourt.experiment.aggregate import authorize
from crashcourt.experiment.controls import (
    C5_METHOD,
    C5_REQUIREMENT,
    ControlReport,
    Threshold,
    judge,
    missing,
    recount,
)
from crashcourt.experiment.gate import NotAuthorized

from ._experiment import (
    C5_REPORT,
    C5_SOURCES,
    CHECKER_SHA,
    SCENARIOS,
    SEEDS,
    FakeChecker,
    FakeScenarios,
    N,
    Repo,
    c5_report,
    commit,
    make_repo,
    prereg_model,
)
from .test_experiment_aggregate import R1_AS_PREDICTED, batch

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "research" / "evidence" / "t-11"


def c5_reasons(repo: Repo) -> list[str]:
    return missing(prereg_model(), repo.prereg_commit, repo.root)


def judged(report: ControlReport) -> list[str]:
    prereg = prereg_model()
    return judge(report, prereg.checker, prereg.environment)


def refused_new_evidence(repo: Repo, instrument: list[Path]) -> str:
    with pytest.raises(NotAuthorized) as caught:
        authorize(
            repo.root / "scenarios/X1.yaml",
            N,
            repo.root,
            instrument,
            checker=FakeChecker(),
            scenarios=FakeScenarios(),
        )
    return "\n".join(caught.value.reasons)


def test_the_methodology_is_three_applicable_mutants_and_two_named_exclusions() -> None:
    assert [(a.id, a.target, a.scenario) for a in C5_METHOD.applicable] == [
        ("M3", "I5a", "scenarios/w1-approve.yaml"),
        ("M4", "I1b", "scenarios/w1-approve.yaml"),
        ("M5", "I6b", "scenarios/w5-sequential.yaml"),
    ]
    assert [(e.id, e.target) for e in C5_METHOD.excluded] == [("M1", "I7a"), ("M2", "I5a")]
    assert C5_METHOD.coverage() == (
        "3 of the 5 specified saver mutants are applicable to the first matrix and measured; "
        "M1, M2 are NOT_EXERCISED and are not counted as passing"
    )
    # The pre-registration's words state the scope, the shapes, and the exclusions.
    for words in ("M3 on W1 approve", "M4 on W1 approve", "M5 on W5 sequential", "NOT EXERCISED"):
        assert words in C5_REQUIREMENT


def test_a_passing_report_allows_new_evidence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert c5_reasons(repo) == []
    auth = authorize(
        repo.root / "scenarios/X1.yaml",
        N,
        repo.root,
        [batch(repo, "inst")],
        checker=FakeChecker(),
        scenarios=FakeScenarios(),
    )
    assert auth.phase == "new-evidence"


def test_a_failed_applicable_mutant_refuses_new_evidence_even_with_the_cells_as_predicted(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, report=c5_report({"M5": 0, "M4": 18}))
    reasons = refused_new_evidence(repo, [batch(repo, "inst")])
    assert "M5 was caught by I6b in 0 of 20 trials; C5 needs at least 19 of 20" in reasons
    assert "M4 was caught by I1b in 18 of 20 trials" in reasons
    assert "M3 was" not in reasons


def test_19_of_20_passes_and_18_does_not() -> None:
    assert recount(c5_report({"M4": 19})) == []
    assert recount(c5_report({"M4": 18})) == [
        "M4 was caught by I1b in 18 of 20 trials; C5 needs at least 19 of 20"
    ]


def test_an_excluded_mutant_is_never_counted() -> None:
    report = c5_report()
    m1 = report.mutants[0].model_copy(update={"id": "M1", "target": "I7a"})
    counted = report.model_copy(update={"mutants": (*report.mutants, m1)})
    reasons = judged(counted)
    assert "it counts M1, which the first matrix does not exercise" in reasons
    five = c5_report(specified=5, applicable=5)
    assert "it says 5 of 5 mutants are applicable, not 3 of 5" in judged(five)


def test_the_exclusions_must_be_recorded_as_not_exercised() -> None:
    report = c5_report()
    silent = report.model_copy(update={"excluded": report.excluded[:1]})
    assert "it records ['M1'] as not exercised, not exactly ['M1', 'M2']" in judged(silent)
    renamed = report.model_copy(
        update={"excluded": (report.excluded[0].model_copy(update={"reason": "skipped"}),)}
    )
    assert "it records M1's exclusion other than the methodology does" in judged(
        renamed.model_copy(update={"excluded": (*renamed.excluded, report.excluded[1])})
    )
    with pytest.raises(ValueError, match="NOT_EXERCISED"):
        ControlReport.model_validate(
            {
                **report.model_dump(),
                "excluded": [{**report.excluded[0].model_dump(), "status": "PASS"}],
            }
        )
    blurred = c5_report(coverage="5 of 5 saver mutants passed")
    assert "its coverage line is not the methodology's" in judged(blurred)


def test_each_applicable_mutant_runs_on_its_preregistered_shape_and_target() -> None:
    report = c5_report()
    m5_on_w1 = report.mutants[2].model_copy(update={"scenario": "scenarios/w1-approve.yaml"})
    reasons = judged(report.model_copy(update={"mutants": (*report.mutants[:2], m5_on_w1)}))
    assert (
        "M5 ran on scenarios/w1-approve.yaml against I6b; the methodology is "
        "scenarios/w5-sequential.yaml against I6b" in reasons
    )
    retargeted = report.mutants[0].model_copy(update={"target": "I8"})
    assert any(
        r.startswith("M3 ran on scenarios/w1-approve.yaml against I8")
        for r in judged(report.model_copy(update={"mutants": (retargeted, *report.mutants[1:])}))
    )
    two = report.model_copy(update={"mutants": report.mutants[:2]})
    assert "it measures ['M3', 'M4'], not exactly ['M3', 'M4', 'M5']" in judged(two)


def test_the_requirement_is_the_methodologys_word_for_word(tmp_path: Path) -> None:
    assert "it states another requirement than the pre-registered C5 methodology" in judged(
        c5_report(requirement="saver mutants M1-M5 each caught")
    )
    loose = prereg_model(
        instrument_checks=[
            {"id": "C5", "requirement": "saver mutants M1-M5", "evidence": C5_REPORT}
        ]
    )
    repo = make_repo(tmp_path, loose)
    assert "instrument check C5's requirement is not the C5 methodology the gate enforces" in (
        refused_new_evidence(repo, [batch(repo, "inst")])
    )


@pytest.mark.parametrize(
    ("instrument_checks", "expected"),
    [
        (
            [{"id": "AM", "requirement": "mutants", "evidence": "ci run 1"}],
            "has no instrument check C5",
        ),
        (
            [{"id": "C5", "requirement": C5_REQUIREMENT, "evidence": "research/elsewhere.json"}],
            "research/elsewhere.json is not a file in the pre-registration commit",
        ),
        (
            [{"id": "C5", "requirement": C5_REQUIREMENT, "evidence": "a CI run"}],
            "a CI run is not a file in the pre-registration commit",
        ),
    ],
)
def test_a_missing_report_refuses_new_evidence(
    tmp_path: Path, instrument_checks: list[dict[str, Any]], expected: str
) -> None:
    repo = make_repo(tmp_path, prereg_model(instrument_checks=instrument_checks))
    assert expected in refused_new_evidence(repo, [batch(repo, "inst")])


def test_a_draft_c5_without_evidence_is_named(tmp_path: Path) -> None:
    # A final pre-registration cannot leave it out; the gate reads only a final one.
    repo = make_repo(tmp_path)
    draft = prereg_model(
        state="draft", instrument_checks=[{"id": "C5", "requirement": C5_REQUIREMENT}]
    )
    assert missing(draft, repo.prereg_commit, repo.root) == [
        "instrument check C5 records no controls report"
    ]


def test_a_report_committed_after_the_preregistration_does_not_count(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, report=c5_report({"M3": 0}))
    commit(repo.root, {C5_REPORT: c5_report().to_json()}, "replace the report")
    reasons = missing(prereg_model(), repo.prereg_commit, repo.root)
    assert any("M3 was caught by I5a in 0 of 20" in r for r in reasons)


def test_a_malformed_report_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    prereg_commit = commit(repo.root, {C5_REPORT: '{"control": "C5"}\n'}, "break it")
    reasons = missing(prereg_model(), prereg_commit, repo.root)
    assert len(reasons) == 1 and "is not a controls report" in reasons[0]


def test_a_report_that_claims_a_pass_its_counts_do_not_show_is_refused() -> None:
    reasons = judged(c5_report({"M5": 3}, applicable_passed=True))
    assert "it says applicable_passed is True, but its counts say otherwise" in reasons
    assert any("M5 was caught by I6b in 3 of 20" in r for r in reasons)


def test_the_threshold_and_the_counts_are_the_specifications() -> None:
    lowered = c5_report(threshold=Threshold(caught=18, trials=20))
    assert "its threshold is 18 of 20, not 19 of 20" in judged(lowered)
    report = c5_report()
    first = report.mutants[0].model_copy(update={"trials": 19, "valid": 19, "caught": 19})
    short = report.model_copy(update={"mutants": (first, *report.mutants[1:])})
    assert "M3 was caught by I5a in 19 of 19 trials; C5 needs at least 19 of 20" in judged(short)
    muddled = report.mutants[0].model_copy(update={"valid": 18})
    assert "M3: inconsistent counts (20 caught, 18 valid)" in recount(
        report.model_copy(update={"mutants": (muddled, *report.mutants[1:])})
    )


def test_a_report_from_another_checker_is_refused() -> None:
    report = c5_report()
    other = report.model_copy(
        update={"checker": report.checker.model_copy(update={"checker_sha256": "9" * 64})}
    )
    assert f"it ran under checker_sha256 {'9' * 64}, the pre-registration pins {CHECKER_SHA}" in (
        judged(other)
    )


def test_a_report_from_another_environment_is_refused() -> None:
    report = c5_report()
    dirty = report.model_copy(update={"manifest": {**report.manifest, "git_dirty": True}})
    assert "it ran with git_dirty True; the pre-registration requires False" in judged(dirty)


@pytest.mark.parametrize("path", list(C5_SOURCES))
def test_the_mutants_driver_and_shapes_that_ran_must_be_the_committed_ones(
    tmp_path: Path, path: str
) -> None:
    repo = make_repo(tmp_path)
    prereg_commit = commit(repo.root, {path: "something else\n"}, "change a source")
    reasons = missing(prereg_model(), prereg_commit, repo.root)
    assert len(reasons) == 1 and f"it ran {path} at" in reasons[0]


def test_a_report_must_name_every_source_it_ran() -> None:
    report = c5_report()
    sources = {k: v for k, v in report.sources.items() if k != "scenarios/w5-sequential.yaml"}
    trimmed = report.model_copy(update={"sources": sources})
    from crashcourt.experiment.controls import _sources  # pyright: ignore[reportPrivateUsage]

    assert "it names no hash for scenarios/w5-sequential.yaml" in _sources(trimmed, ROOT, "HEAD")


def c3_prereg() -> Any:
    """R1 and X1 plus C3, a placebo control (failpoints armed with action none, a 0 ms latency
    toxic), predicted to show no anomaly anywhere."""
    data = prereg_model().model_dump(mode="json")
    r1, x1 = data["cells"]
    c3 = {
        **r1,
        "id": "C3",
        "evidence": "control",
        "replication_of": None,
        "control": "C3",
        "fault": None,
        "trigger": "every new-evidence failpoint armed with action none; a 0 ms latency toxic",
        "scenario": "scenarios/C3.yaml",
        "scenario_sha256": SCENARIOS["C3"],
        "master_seed": SEEDS["C3"],
        "observations": [],
        "predicted": [{"invariant": "*", "expect": ["PASS", "NO_ANOMALY_UNSPECIFIED"]}],
    }
    return prereg_model(cells=[r1, c3, x1])


def test_a_missing_or_failed_placebo_control_refuses_new_evidence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, c3_prereg())
    r1 = batch(repo, "r1", [R1_AS_PREDICTED] * N)
    assert "instrument cell C3 has no batch" in refused_new_evidence(repo, [r1])
    anomaly = [("I4", "NO_ANOMALY_UNSPECIFIED"), ("I5a", "GAP")]
    c3 = batch(repo, "c3", [anomaly] * N, cell="C3")
    reasons = refused_new_evidence(repo, [r1, c3])
    assert "instrument cell C3 is differs, not as predicted" in reasons
    quiet = [("I4", "NO_ANOMALY_UNSPECIFIED"), ("I5a", "PASS")]
    auth = authorize(
        repo.root / "scenarios/X1.yaml",
        N,
        repo.root,
        [r1, batch(repo, "c3-quiet", [quiet] * N, cell="C3")],
        checker=FakeChecker(),
        scenarios=FakeScenarios(),
    )
    assert auth.instrument_batches == ("c3-quiet", "r1")


def test_the_original_c5_measurement_is_kept_as_it_was_and_the_gate_never_reads_it() -> None:
    original = json.loads((EVIDENCE / "c5-original.json").read_text(encoding="utf-8"))
    shown = {m["id"]: (m["caught"], m["trials"], m["target_outcomes"]) for m in original["mutants"]}
    assert shown == {
        "M1": (0, 20, {"NO_ANOMALY_UNSPECIFIED": 20}),
        "M2": (0, 20, {"NO_ANOMALY_UNSPECIFIED": 20}),
        "M3": (20, 20, {"GAP": 20}),
        "M4": (20, 20, {"VIOLATION_CANDIDATE": 20}),
        "M5": (0, 20, {"INVALID": 20}),
    }
    assert original["passed"] is False and original["run_commit"].startswith("ae691a2")
    exploratory = json.loads((EVIDENCE / "m5-w5-exploratory.json").read_text(encoding="utf-8"))
    assert exploratory["counts_toward_c5"] is False
    assert (exploratory["result"]["caught"], exploratory["result"]["trials"]) == (20, 20)
    with pytest.raises(ValueError):
        ControlReport.model_validate(original)  # the original format is a record, not evidence


def test_the_recorded_revised_report_passes_with_its_exclusions_and_matches_the_sources() -> None:
    from crashcourt.experiment import gitproof
    from crashcourt.experiment.preregistration import PREREGISTRATION_PATH, parse

    report = ControlReport.model_validate_json((EVIDENCE / "c5.json").read_bytes())
    draft = parse((ROOT / PREREGISTRATION_PATH).read_text(encoding="utf-8"))
    [check] = [c for c in draft.instrument_checks if c.id == "C5"]
    assert check.requirement == report.requirement == C5_REQUIREMENT
    # Only the environment values the draft has not pinned yet stand in the way.
    reasons = judge(report, draft.checker, draft.environment)
    assert reasons and all(r.endswith("the pre-registration requires None") for r in reasons)
    assert report.applicable_passed and recount(report) == []
    assert [(e.id, e.status) for e in report.excluded] == [
        ("M1", "NOT_EXERCISED"),
        ("M2", "NOT_EXERCISED"),
    ]
    assert (report.applicable, report.specified, report.coverage) == (3, 5, C5_METHOD.coverage())
    for path, digest in report.sources.items():
        assert gitproof.blob_sha256(ROOT, "HEAD", path) == digest, path
