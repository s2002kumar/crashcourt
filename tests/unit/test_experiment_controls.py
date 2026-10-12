"""The controls the gate reads before new evidence: C5's report, and the C3, K1a, and K3 instrument
cells. Every failed, missing, or mismatched control must refuse a new-evidence batch, against a
real git repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crashcourt.experiment.aggregate import authorize
from crashcourt.experiment.controls import C5_MUTANTS, Threshold, judge, missing, recount
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


def c5_reasons(repo: Repo) -> list[str]:
    return missing(prereg_model(), repo.prereg_commit, repo.root)


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


def test_a_failed_c5_refuses_new_evidence_even_with_the_instrument_cells_as_predicted(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, report=c5_report({"M1": 0, "M2": 18}))
    reasons = refused_new_evidence(repo, [batch(repo, "inst")])
    assert "M1 was caught by I5a in 0 of 20 trials; C5 needs at least 19 of 20" in reasons
    assert "M2 was caught by I5a in 18 of 20 trials" in reasons
    assert "M3" not in reasons


def test_19_of_20_passes_and_18_does_not() -> None:
    assert recount(c5_report({"M4": 19})) == []
    assert recount(c5_report({"M4": 18})) == [
        "M4 was caught by I5a in 18 of 20 trials; C5 needs at least 19 of 20"
    ]


@pytest.mark.parametrize(
    ("instrument_checks", "expected"),
    [
        (
            [{"id": "AM", "requirement": "mutants", "evidence": "ci run 1"}],
            "has no instrument check C5",
        ),
        (
            [{"id": "C5", "requirement": "saver mutants", "evidence": "research/elsewhere.json"}],
            "research/elsewhere.json is not a file in the pre-registration commit",
        ),
        (
            [{"id": "C5", "requirement": "saver mutants", "evidence": "a CI run"}],
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
        state="draft", instrument_checks=[{"id": "C5", "requirement": "saver mutants"}]
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
    report = c5_report({"M5": 3}, passed=True)
    reasons = judge(report, prereg_model().checker, prereg_model().environment)
    assert "it says passed is True, but its counts say otherwise" in reasons
    assert any("M5 was caught by I5a in 3 of 20" in r for r in reasons)


def test_the_threshold_and_the_mutants_are_the_specifications() -> None:
    prereg = prereg_model()
    lowered = c5_report(threshold=Threshold(caught=18, trials=20))
    assert "its threshold is 18 of 20, not 19 of 20" in judge(
        lowered, prereg.checker, prereg.environment
    )
    four = c5_report()
    four = four.model_copy(update={"mutants": four.mutants[:4]})
    assert f"it covers {list(C5_MUTANTS[:4])}, not exactly {list(C5_MUTANTS)}" in judge(
        four, prereg.checker, prereg.environment
    )
    report = c5_report()
    first = report.mutants[0].model_copy(update={"trials": 19, "valid": 19, "caught": 19})
    short = report.model_copy(update={"mutants": (first, *report.mutants[1:])})
    assert "M1 was caught by I5a in 19 of 19 trials; C5 needs at least 19 of 20" in judge(
        short, prereg.checker, prereg.environment
    )
    muddled = report.mutants[0].model_copy(update={"valid": 18})
    assert "M1: inconsistent counts (20 caught, 18 valid)" in recount(
        report.model_copy(update={"mutants": (muddled, *report.mutants[1:])})
    )


def test_a_report_from_another_checker_is_refused() -> None:
    prereg = prereg_model()
    report = c5_report()
    other = report.model_copy(
        update={"checker": report.checker.model_copy(update={"checker_sha256": "9" * 64})}
    )
    assert f"it ran under checker_sha256 {'9' * 64}, the pre-registration pins {CHECKER_SHA}" in (
        judge(other, prereg.checker, prereg.environment)
    )


def test_a_report_from_another_environment_is_refused() -> None:
    prereg = prereg_model()
    report = c5_report()
    dirty = report.model_copy(update={"manifest": {**report.manifest, "git_dirty": True}})
    assert "it ran with git_dirty True; the pre-registration requires False" in judge(
        dirty, prereg.checker, prereg.environment
    )


def test_the_mutants_that_ran_must_be_the_committed_ones(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    path = next(iter(C5_SOURCES))
    prereg_commit = commit(repo.root, {path: "a different defect\n"}, "change a mutant")
    reasons = missing(prereg_model(), prereg_commit, repo.root)
    assert len(reasons) == 1 and f"it ran {path} at" in reasons[0]


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


def test_the_recorded_c5_report_is_one_the_gate_reads_and_it_fails() -> None:
    from crashcourt.experiment.controls import ControlReport
    from crashcourt.experiment.preregistration import PREREGISTRATION_PATH, parse

    root = Path(__file__).resolve().parents[2]
    raw = (root / "research" / "evidence" / "t-11" / "c5.json").read_bytes()
    report = ControlReport.model_validate_json(raw)
    draft = parse((root / PREREGISTRATION_PATH).read_text(encoding="utf-8"))
    assert draft.checker is not None
    assert report.checker.checker_sha256 == draft.checker.checker_sha256
    assert report.checker.frozen_md_sha256 == draft.checker.frozen_md_sha256
    assert not report.passed
    assert [r.split(" was")[0] for r in recount(report)] == ["M1", "M2", "M5"]
