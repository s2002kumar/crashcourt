from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from crashcourt.experiment.fingerprint import CompatibilityRule, fingerprint
from crashcourt.experiment.gate import (
    FROZEN_PATH,
    FrozenChecker,
    InstrumentEvidence,
    NotAuthorized,
    ScenarioFacts,
    T07Scenarios,
    Unavailable,
    authorize_batch,
    confirm,
    confirm_environment,
)
from crashcourt.experiment.preregistration import (
    BUILDER_PATH,
    PREREGISTRATION_PATH,
    Preregistration,
)

from ._experiment import (
    BUILDER_SHA,
    BUILDER_TEXT,
    CHECKER_SHA,
    EFFECT_MODELS,
    FROZEN_MD,
    FROZEN_MD_SHA,
    LANDMARK_SHA,
    LANDMARK_TABLES,
    REGISTRY_SHA,
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
    ) -> InstrumentEvidence:
        return InstrumentEvidence(
            list(instrument_missing or []), ["inst-batch"], fingerprint(manifest_for("0" * 40))
        )

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


def test_a_final_preregistration_records_its_state(tmp_path: Path) -> None:
    assert authorize(make_repo(tmp_path)).preregistration_state == "final"


def test_a_draft_authorizes_a_complete_instrument_cell(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, prereg_model(state="draft"))
    auth = authorize(repo)
    assert (auth.cell, auth.phase, auth.preregistration_state) == ("R1", "instrument", "draft")


def test_a_draft_never_authorizes_new_evidence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, prereg_model(state="draft"))
    reasons = refused(authorize, repo, "X1", instrument_missing=[])
    assert "the pre-registration is a draft; new-evidence cell X1 needs it final" in reasons


def _r1_with(**change: object) -> list[dict[str, object]]:
    cells = prereg_model().model_dump(mode="json")["cells"]
    cells[0].update(change)
    return cells


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"checker": None}, "no frozen checker is named"),
        ({"builder": None}, "no TrialScenario builder is pinned"),
        (
            {"environment": {"os_system": "Linux", "runner": None}},
            "environment runner is not pinned",
        ),
        ({"cells": _r1_with(scenario_sha256=None)}, "cell R1: no scenario hash"),
        ({"cells": _r1_with(master_seed=None)}, "cell R1: no master seed"),
        ({"cells": _r1_with(pending="an audit")}, "cell R1: pending an audit"),
    ],
)
def test_a_draft_refuses_an_incomplete_cell_or_incomplete_pins(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    repo = make_repo(tmp_path, prereg_model(state="draft", **change))
    assert f"the pre-registration is a draft and {message}" in refused(authorize, repo)


def test_a_dirty_checkout_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo.root / "scenarios/R1.yaml").write_text("cell: R1\nedited: true\n", encoding="utf-8")
    assert "the checkout is not clean" in refused(authorize, repo)


def test_no_frozen_checker_means_no_run(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert "the checker is PRE-FREEZE, not FROZEN" in refused(
        authorize, repo, checker=FakeChecker(state="PRE-FREEZE")
    )


def test_the_default_adapters_refuse_a_freeze_that_is_not_the_checkers(tmp_path: Path) -> None:
    """With T-10's frozen checker in the checkout, the default adapter verifies it against the
    repository's FROZEN.md; this fixture's freeze is not the checker's, so the batch is refused."""
    repo = make_repo(tmp_path)
    reasons = refused(authorize_batch, repo.root / "scenarios/R1.yaml", N, repo.root)
    assert f"the checker does not match {FROZEN_PATH}" in reasons


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
        (ScenarioFacts("9" * 64, "R1", "seed-r1", 2), "not the pre-registered"),
        (ScenarioFacts("1" * 64, "R2", "seed-r1", 2), "names cell R2"),
        (ScenarioFacts("1" * 64, "R1", "another-seed", 2), "master seed another-seed"),
        # Same hash, so only the workers differ: the cell's count must be the scenario's.
        (
            ScenarioFacts(SCENARIOS["R1"], "R1", "seed-r1", 1),
            "declares 1 worker; cell R1 is pre-registered with 2",
        ),
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


def test_a_fault_cell_must_name_a_landmark_table(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert "cell R1 injects F01, so its scenario must name a landmark table" in refused(
        authorize, repo, checker=FakeChecker(landmark=None)
    )


def test_a_cell_without_a_fault_may_name_no_landmark_table(tmp_path: Path) -> None:
    cells = prereg_model().model_dump(mode="json")["cells"]
    cells[0]["fault"] = None
    repo = make_repo(tmp_path, prereg_model(cells=cells))
    auth = authorize(repo, checker=FakeChecker(landmark=None))
    assert auth.landmark_table_sha256 is None


def test_every_hash_the_freeze_records_must_be_the_pinned_one(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    for changed in (
        FrozenChecker("v1", CHECKER_SHA, REGISTRY_SHA, {"w1": "9" * 64}, dict(LANDMARK_TABLES)),
        FrozenChecker("v1", CHECKER_SHA, REGISTRY_SHA, dict(EFFECT_MODELS), {}),
        FrozenChecker("v2", CHECKER_SHA, REGISTRY_SHA, dict(EFFECT_MODELS), dict(LANDMARK_TABLES)),
    ):
        assert "the pre-registration names" in refused(
            authorize, repo, checker=FakeChecker(frozen=changed)
        )


def test_the_landmark_table_must_be_a_pinned_one(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert "is not one the freeze pins" in refused(
        authorize, repo, checker=FakeChecker(landmark="5" * 64)
    )


def test_new_evidence_must_run_where_the_instrument_ran(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    auth = authorize(repo, cell="X1", instrument_missing=[])
    assert auth.instrument_fingerprint is not None
    same = manifest_for(auth.run_commit)
    prereg = prereg_model()
    # The stand-in instrument ran at commit 000...0; everything else matches.
    found = confirm_environment(prereg, auth, same)
    assert found == [f"git_sha is {auth.run_commit!r}; the instrument cells ran with {'0' * 40!r}"]
    rule = CompatibilityRule(
        field="git_sha", values=("0" * 40, auth.run_commit), reason="docs only"
    )
    assert confirm_environment(prereg, auth, same, [rule]) == []
    other = manifest_for(auth.run_commit, os_release="6.11.0")
    assert any("os_release" in p for p in confirm_environment(prereg, auth, other, [rule]))


def test_new_evidence_must_run_on_the_instrument_cells_host(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    auth = authorize(repo, "X1", instrument_missing=[])
    prereg = prereg_model()
    manifest = manifest_for(auth.run_commit)
    host = {"cpu_count": 4, "memory_gib": 16}
    hosted = auth.model_copy(update={"instrument_fingerprint": fingerprint(manifest, host)})
    assert confirm_environment(prereg, hosted, manifest, [], host) == []
    smaller = confirm_environment(prereg, hosted, manifest, [], {"cpu_count": 2, "memory_gib": 8})
    assert "host.cpu_count is 2; the instrument cells ran with 4" in smaller
    assert "host.memory_gib is 8; the instrument cells ran with 16" in smaller
    assert any("host.cpu_count is None" in p for p in confirm_environment(prereg, hosted, manifest))


def test_the_runner_scenario_loader_is_the_real_one() -> None:
    from crashcourt.spec.scenario import load_scenario, scenario_sha256

    smoke = Path(__file__).resolve().parents[2] / "scenarios" / "smoke.yaml"
    facts = T07Scenarios().load(smoke)
    loaded = load_scenario(smoke)
    assert facts == ScenarioFacts(
        scenario_sha256(loaded), loaded.cell, loaded.seed, len(loaded.workers)
    )


# -- the TrialScenario builder ------------------------------------------------------------------


def test_a_builder_changed_after_the_preregistration_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert authorize(repo).run_commit == repo.prereg_commit
    commit(repo.root, {BUILDER_PATH: BUILDER_TEXT + "# changed\n"}, "change the builder")
    reasons = refused(authorize, repo)
    assert f"{BUILDER_PATH} at the run commit hashes to " in reasons
    assert f"not the pre-registered {BUILDER_SHA}" in reasons
    assert "pre-registration commit hashes" not in reasons


def test_a_preregistration_must_pin_the_builder_its_own_commit_holds(tmp_path: Path) -> None:
    other = "0" * 64
    repo = make_repo(tmp_path, prereg_model(builder={"path": BUILDER_PATH, "sha256": other}))
    reasons = refused(authorize, repo)
    for name in ("pre-registration", "run"):
        assert (
            f"{BUILDER_PATH} at the {name} commit hashes to {BUILDER_SHA}, not the "
            f"pre-registered {other}"
        ) in reasons


def test_a_builder_missing_at_the_run_commit_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo.root, "rm", "-q", BUILDER_PATH)
    git(repo.root, "commit", "-q", "-m", "remove the builder")
    assert f"{BUILDER_PATH} at the run commit hashes to None" in refused(authorize, repo)
