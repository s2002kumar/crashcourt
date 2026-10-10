"""`crashcourt run` and the pre-registration gate: which batches it covers; that it fails closed."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from crashcourt.experiment import aggregate, gate
from crashcourt.experiment.gate import Authorization, NotAuthorized
from crashcourt.orchestrator import preregistered, runner
from crashcourt.orchestrator.preregistered import GateRefused, PreregisteredCell
from crashcourt.orchestrator.runner import RunOptions, RunRefused, Settings, run_batch
from crashcourt.spec.scenario import Scenario, parse_scenario, scenario_sha256

from ._experiment import commit, make_repo, manifest_for, prereg_model, prereg_text

SCENARIO: dict[str, Any] = {
    "v": 1,
    "name": "gate",
    "cell": "unlisted",
    "seed": "seed",
    "workload": "smoke",
    "target": {"saver": "postgres", "driver": "async", "durability": "sync"},
    "workers": ["w1"],
    "ops": [{"id": "op-1", "worker": "w1", "thread": "t1", "after": "start"}],
}


def scenario(**changes: Any) -> Scenario:
    data = copy.deepcopy(SCENARIO)
    data.update(changes)
    return parse_scenario(data)


# -- which batches the gate covers ------------------------------------------------------------


def test_without_any_preregistration_nothing_is_gated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    commit_init(repo)
    assert preregistered.preregistered_cell(scenario(), repo / "s.yaml", repo) is None


def commit_init(repo: Path) -> None:
    from ._experiment import git

    git(repo, "init", "-q", "-b", "main")
    commit(repo, {"README.md": "x\n"}, "init")


def test_a_preregistered_file_is_gated(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    cell = preregistered.preregistered_cell(scenario(), repo.root / "scenarios/R1.yaml", repo.root)
    assert cell == PreregisteredCell("R1", "instrument", "scenarios/R1.yaml")


def test_a_copy_elsewhere_is_still_gated_by_its_cell_id(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    cell = preregistered.preregistered_cell(scenario(cell="X1"), tmp_path / "copy.yaml", repo.root)
    assert cell is not None and cell.id == "X1" and cell.phase == "new-evidence"


def test_a_copy_under_another_cell_id_is_still_gated_by_its_content(tmp_path: Path) -> None:
    disguised = scenario(cell="renamed")
    data = prereg_model().model_dump(mode="json")
    data["cells"][0]["scenario_sha256"] = scenario_sha256(disguised)
    repo = make_repo(tmp_path, prereg_model(**{"cells": data["cells"]}))
    cell = preregistered.preregistered_cell(disguised, tmp_path / "copy.yaml", repo.root)
    assert cell is not None and cell.id == "R1"


def test_an_unlisted_scenario_is_not_gated(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert preregistered.preregistered_cell(scenario(), repo.root / "s.yaml", repo.root) is None


def test_a_working_tree_edit_cannot_take_a_cell_out_of_the_gate(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    data = prereg_model().model_dump(mode="json")
    data["cells"] = data["cells"][1:]  # R1 removed, but only in the working tree
    path = repo.root / "research" / "preregistration.md"
    path.write_text(prereg_text(prereg_model(**{"cells": data["cells"]})), encoding="utf-8")
    cell = preregistered.preregistered_cell(scenario(), repo.root / "scenarios/R1.yaml", repo.root)
    assert cell is not None and cell.id == "R1"


def test_an_uncommitted_preregistration_still_gates(tmp_path: Path) -> None:
    # A pre-registration only in the working tree (never committed) still names its cells.
    repo = tmp_path / "repo"
    repo.mkdir()
    commit_init(repo)
    path = repo / "research" / "preregistration.md"
    path.parent.mkdir()
    path.write_text(prereg_text(prereg_model(state="draft", checker=None)), encoding="utf-8")
    cell = preregistered.preregistered_cell(scenario(cell="R1"), tmp_path / "x.yaml", repo)
    assert cell is not None and cell.id == "R1"


def test_an_unreadable_working_tree_preregistration_refuses_the_batch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo.root / "research" / "preregistration.md").write_text("# no block", encoding="utf-8")
    with pytest.raises(GateRefused, match="in the working tree cannot be read"):
        preregistered.preregistered_cell(scenario(), repo.root / "s.yaml", repo.root)


def test_an_unreadable_committed_preregistration_refuses_the_batch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo.root, {"research/preregistration.md": "# no block here\n"}, "break it")
    with pytest.raises(GateRefused, match="cannot be decided"):
        preregistered.preregistered_cell(scenario(), repo.root / "s.yaml", repo.root)


# -- what a gated batch needs -----------------------------------------------------------------

R1 = PreregisteredCell("R1", "instrument", "scenarios/R1.yaml")


def test_a_gated_cell_runs_only_from_its_own_file(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GateRefused, match=r"runs only from its scenario file scenarios/R1\.yaml"):
        preregistered.authorize(R1, tmp_path / "copy.yaml", 3, repo.root, canonical=True)
    with pytest.raises(GateRefused, match="scenario file"):
        preregistered.authorize(R1, None, 3, repo.root, canonical=True)


def test_a_gated_cell_runs_only_as_a_canonical_batch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GateRefused, match="canonical"):
        preregistered.authorize(R1, repo.root / "scenarios/R1.yaml", 3, repo.root, canonical=False)


def test_today_a_draft_and_no_frozen_checker_refuse_every_gated_batch(tmp_path: Path) -> None:
    # The real gate, with no stand-ins: this checkout has no frozen checker, and the
    # pre-registration is a draft.
    draft = prereg_model(state="draft", checker=None)
    repo = make_repo(tmp_path, draft)
    with pytest.raises(GateRefused) as refused:
        preregistered.authorize(R1, repo.root / "scenarios/R1.yaml", 3, repo.root, canonical=True)
    text = str(refused.value)
    assert "pre-registered cell R1 is not authorized" in text
    assert "draft" in text and "frozen checker" in text


def test_every_reason_the_gate_gives_is_passed_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)

    def refuse(*args: object, **kwargs: object) -> Authorization:
        raise NotAuthorized(
            ["the checker is DRAFT, not FROZEN", "cell R1 is pre-registered at N = 3"]
        )

    monkeypatch.setattr(aggregate, "authorize", refuse)
    with pytest.raises(GateRefused) as refused:
        preregistered.authorize(R1, repo.root / "scenarios/R1.yaml", 5, repo.root, canonical=True)
    assert "the checker is DRAFT, not FROZEN; cell R1 is pre-registered at N = 3" in str(
        refused.value
    )


def authorization(run_commit: str) -> Authorization:
    return Authorization(
        cell="R1",
        phase="instrument",
        evidence="replication",
        scenario="scenarios/R1.yaml",
        scenario_sha256="1" * 64,
        master_seed="seed-r1",
        trials=3,
        run_commit=run_commit,
        preregistration_commit="a" * 40,
        preregistration_sha256="b" * 64,
        freeze_commit="c" * 40,
        frozen_md_sha256="d" * 64,
        checker_version="v1",
        checker_sha256="e" * 64,
        registry_sha256="f" * 64,
        landmark_table_sha256="0" * 64,
    )


def test_an_authorized_batch_gets_its_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    granted = authorization(repo.prereg_commit)
    seen: list[tuple[Any, ...]] = []

    def grant(*args: Any) -> Authorization:
        seen.append(args)
        return granted

    monkeypatch.setattr(aggregate, "authorize", grant)
    instrument = (tmp_path / "runs" / "c1-batch",)
    source = repo.root / "scenarios/R1.yaml"
    assert (
        preregistered.authorize(R1, source, 3, repo.root, canonical=True, instrument=instrument)
        is granted
    )
    # The instrument batches a new-evidence cell rests on reach the gate unchanged.
    assert seen == [(source, 3, repo.root, instrument)]


def test_a_manifest_outside_the_preregistered_environment_refuses_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    granted = authorization("1" * 40)
    monkeypatch.setattr(gate, "confirm", lambda *a: ["the manifest records a dirty checkout"])
    with pytest.raises(GateRefused, match="not the pre-registered one: the manifest records"):
        preregistered.confirm(granted, manifest_for("1" * 40), tmp_path)
    monkeypatch.setattr(gate, "confirm", lambda *a: [])
    preregistered.confirm(granted, manifest_for("1" * 40), tmp_path)


# -- crashcourt run ---------------------------------------------------------------------------


def test_run_refuses_a_gated_batch_before_doctor_or_any_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path, prereg_model(state="draft", checker=None))
    monkeypatch.setattr(runner, "require_linux", lambda: None)

    def no_doctor(*args: object) -> object:
        raise AssertionError("doctor ran for a batch the gate refused")

    monkeypatch.setattr(runner, "run_doctor", no_doctor)
    settings = Settings.from_environment(repo.root, {})
    with pytest.raises(RunRefused, match="pre-registered cell R1 is not authorized"):
        run_batch(
            scenario(cell="R1"),
            RunOptions(trials=3, out=tmp_path / "runs", canonical=True),
            settings,
            source=repo.root / "scenarios/R1.yaml",
        )
    assert not (tmp_path / "runs").exists()


# -- the checkout must not change during a gated batch ---------------------------------------


def test_the_authorized_checkout_must_stay_as_it_was(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    granted = authorization(repo.prereg_commit)
    assert preregistered.still_authorized(granted, repo.root) == []
    (repo.root / "scenarios" / "R1.yaml").write_text("cell: R1\nedited: true\n", encoding="utf-8")
    assert preregistered.still_authorized(granted, repo.root) == [
        "the checkout changed during the batch (1 changed paths)"
    ]
    moved = commit(repo.root, {"scenarios/R1.yaml": "cell: R1\nedited: true\n"}, "edit mid-batch")
    assert preregistered.still_authorized(granted, repo.root) == [
        f"HEAD moved to {moved} during the batch; it was authorized at {repo.prereg_commit}"
    ]


def test_a_revoked_authorization_is_never_left_in_place(tmp_path: Path) -> None:
    batch = tmp_path / "batch"
    batch.mkdir()
    granted = authorization("1" * 40)
    (batch / runner.AUTHORIZATION_FILE).write_text(granted.to_json(), encoding="utf-8")
    preregistered.revoke(batch, runner.AUTHORIZATION_FILE, ["HEAD moved"])
    assert not (batch / runner.AUTHORIZATION_FILE).exists()
    revoked = json.loads((batch / preregistered.REVOKED_FILE).read_text(encoding="utf-8"))
    assert revoked["revoked"] == ["HEAD moved"] and revoked["cell"] == "R1"


def trial(number: int) -> runner.TrialResult:
    return runner.TrialResult(number=number, directory=Path(f"trial-{number:05d}"), valid=True)


def test_a_changed_checkout_stops_the_batch_before_the_next_trial() -> None:
    answers = iter([[], [], ["HEAD moved"]])
    ran: list[int] = []

    async def run_one(number: int) -> runner.TrialResult:
        ran.append(number)
        return trial(number)

    results, revoked = asyncio.run(runner.run_trials(run_one, 5, lambda: next(answers)))
    assert ran == [1, 2] and [r.number for r in results] == [1, 2]
    assert revoked == ["HEAD moved"]


def test_the_checkout_is_checked_after_the_last_trial_too() -> None:
    answers = iter([[], [], ["the checkout changed"]])

    async def run_one(number: int) -> runner.TrialResult:
        return trial(number)

    results, revoked = asyncio.run(runner.run_trials(run_one, 2, lambda: next(answers)))
    assert len(results) == 2 and revoked == ["the checkout changed"]


def test_a_revoked_batch_is_not_valid_and_its_summary_says_why() -> None:
    summary = runner.BatchSummary("b", Path("b"), [trial(1)], ("HEAD moved",))
    assert not summary.valid
    record = runner._summary_record(summary, scenario())  # pyright: ignore[reportPrivateUsage]
    assert record["authorization_revoked"] == ["HEAD moved"]
    assert "authorization_revoked" not in runner._summary_record(  # pyright: ignore[reportPrivateUsage]
        runner.BatchSummary("b", Path("b"), [trial(1)]), scenario()
    )
