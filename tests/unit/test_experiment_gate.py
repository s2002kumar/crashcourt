from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from crashcourt.experiment.gate import (
    FROZEN_PATH,
    NotAuthorized,
    ScenarioFacts,
    Unavailable,
    authorize_batch,
    confirm,
    confirm_environment,
)
from crashcourt.experiment.preregistration import PREREGISTRATION_PATH, Preregistration

from ._experiment import (
    CHECKER_SHA,
    FROZEN_MD,
    FROZEN_MD_SHA,
    LANDMARK_SHA,
    SCENARIOS,
    FakeChecker,
    FakeScenarios,
    N,
    Repo,
    commit,
    git,
    make_repo,
    manifest_for,
    prereg_model,
    prereg_text,
)


def authorize(
    repo: Repo,
    cell: str = "R1",
    trials: int = N,
    *,
    checker: FakeChecker | None = None,
    scenarios: FakeScenarios | None = None,
    instrument_missing: Sequence[str] | None = None,
):
    def instrument_check(
        prereg: Preregistration, commit_: str, dirs: Sequence[Path]
    ) -> tuple[list[str], list[str]]:
        return list(instrument_missing or []), ["inst-batch"]

    return authorize_batch(
        repo.root / f"scenarios/{cell}.yaml",
        trials,
        repo.root,
        checker=checker or FakeChecker(),
        scenarios=scenarios or FakeScenarios(),
        instrument_check=None if instrument_missing is None else instrument_check,
    )


def refused(fn: object, *args: object, **kwargs: object) -> str:
    with pytest.raises(NotAuthorized) as caught:
        fn(*args, **kwargs)  # type: ignore[operator]
    return "\n".join(caught.value.reasons)


def test_a_preregistered_instrument_cell_is_authorized(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    auth = authorize(repo)
    assert auth.cell == "R1" and auth.phase == "instrument" and auth.trials == N
    assert auth.run_commit == repo.prereg_commit
    assert auth.preregistration_commit == repo.prereg_commit
    assert auth.freeze_commit == repo.freeze_commit
    assert auth.frozen_md_sha256 == FROZEN_MD_SHA
    assert auth.checker_sha256 == CHECKER_SHA
    assert auth.scenario_sha256 == SCENARIOS["R1"]
    assert auth.landmark_table_sha256 == LANDMARK_SHA


def test_later_commits_that_leave_the_preregistration_alone_still_run(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    later = commit(repo.root, {"notes.txt": "unrelated\n"})
    auth = authorize(repo)
    assert (auth.run_commit, auth.preregistration_commit) == (later, repo.prereg_commit)


def test_a_draft_authorizes_nothing(tmp_path: Path) -> None:
    draft = prereg_model(state="draft", checker=None)
    reasons = refused(authorize, make_repo(tmp_path, draft))
    assert "the pre-registration is a draft, not final" in reasons
    assert "names no frozen checker" in reasons


def test_a_dirty_checkout_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo.root / "scenarios/R1.yaml").write_text("cell: R1\nedited: true\n", encoding="utf-8")
    assert "the checkout is not clean" in refused(authorize, repo)


def test_no_frozen_checker_means_no_run(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert "the checker is PRE-FREEZE, not FROZEN" in refused(
        authorize, repo, checker=FakeChecker(state="PRE-FREEZE")
    )


def test_the_default_adapters_refuse_where_the_checker_is_absent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    reasons = refused(authorize_batch, repo.root / "scenarios/R1.yaml", N, repo.root)
    assert "no frozen checker" in reasons
    assert "not in this checkout" in reasons


def test_a_checker_that_does_not_match_its_freeze_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    reasons = refused(authorize, repo, checker=FakeChecker(mismatch="files[engine.py] differ"))
    assert "does not match src/crashcourt/checker/FROZEN.md: files[engine.py] differ" in reasons


def test_a_changed_freeze_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo.root, {str(FROZEN_PATH): FROZEN_MD + "\nedited\n"}, "edit the freeze")
    reasons = refused(authorize, repo)
    assert "not the pre-registered" in reasons
    assert "is not an ancestor of the pre-registration" in reasons


def test_a_freeze_after_the_preregistration_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    files = {f"scenarios/{c}.yaml": f"cell: {c}\n" for c in SCENARIOS}
    files[str(PREREGISTRATION_PATH)] = prereg_text(prereg_model())
    prereg_commit = commit(root, files, "pre-register first")
    freeze = commit(root, {str(FROZEN_PATH): FROZEN_MD}, "freeze afterwards")
    reasons = refused(authorize, Repo(root, freeze, prereg_commit))
    assert f"freeze commit {freeze} is not an ancestor of the pre-registration" in reasons


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        (ScenarioFacts("9" * 64, "R1", "seed-r1"), "not the pre-registered"),
        (ScenarioFacts("1" * 64, "R2", "seed-r1"), "names cell R2"),
        (ScenarioFacts("1" * 64, "R1", "another-seed"), "master seed another-seed"),
    ],
)
def test_the_scenario_must_be_the_preregistered_one(
    tmp_path: Path, facts: ScenarioFacts, message: str
) -> None:
    scenarios = FakeScenarios()
    scenarios.facts["R1"] = facts
    assert message in refused(authorize, make_repo(tmp_path), scenarios=scenarios)


def test_n_is_fixed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert f"pre-registered at N = {N}, not {N + 1}" in refused(authorize, repo, trials=N + 1)
    assert f"pre-registered at N = {N}, not 1" in refused(authorize, repo, trials=1)


def test_an_unregistered_scenario_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo.root, {"scenarios/Z9.yaml": "cell: Z9\n"})
    assert "scenarios/Z9.yaml is not a pre-registered scenario" in refused(
        authorize, repo, cell="Z9"
    )


def test_an_unverifiable_landmark_table_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    checker = FakeChecker(landmark=Unavailable("not wired yet"))
    assert "cannot verify the landmark table: not wired yet" in refused(
        authorize, repo, checker=checker
    )


def test_new_evidence_waits_for_the_instrument(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert "the instrument cells' results were not given" in refused(authorize, repo, cell="X1")
    assert "instrument cell R1 has no batch" in refused(
        authorize, repo, cell="X1", instrument_missing=["instrument cell R1 has no batch"]
    )
    auth = authorize(repo, cell="X1", instrument_missing=[])
    assert auth.phase == "new-evidence" and auth.instrument_batches == ("inst-batch",)


def test_the_environment_is_confirmed_after_doctor(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    auth = authorize(repo)
    prereg = prereg_model()
    assert confirm_environment(prereg, auth, manifest_for(auth.run_commit)) == []
    problems = confirm_environment(
        prereg, auth, manifest_for("0" * 40, runner="github-hosted", git_dirty=True)
    )
    assert any("runner" in p for p in problems)
    assert any("authorized" in p for p in problems)
    assert "the manifest records a dirty checkout" in problems


def test_the_runner_confirms_against_the_committed_preregistration(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    auth = authorize(repo)
    assert confirm(auth, manifest_for(auth.run_commit), repo.root) == []
    assert any(
        "os_system" in p
        for p in confirm(auth, manifest_for(auth.run_commit, os_system="Darwin"), repo.root)
    )
