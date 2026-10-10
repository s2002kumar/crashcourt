from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from crashcourt.cli import main
from crashcourt.experiment.aggregate import AggregationRefused, aggregate, instrument_ready
from crashcourt.experiment.fingerprint import CompatibilityRule
from crashcourt.experiment.gate import Authorization
from crashcourt.experiment.preregistration import PREREGISTRATION_PATH
from crashcourt.experiment.verify import BatchNotVerified, verify_batch

from ._experiment import (
    FakeChecker,
    FakeScenarios,
    N,
    Repo,
    commit,
    make_batch,
    make_repo,
    manifest_for,
    prereg_model,
)

R1_AS_PREDICTED = [("I4", "EXPECTED"), ("I1b", "PASS")]


def authorized(repo: Repo, cell: str = "R1") -> Authorization:
    from crashcourt.experiment.gate import authorize_batch

    return authorize_batch(
        repo.root / f"scenarios/{cell}.yaml",
        N,
        repo.root,
        checker=FakeChecker(),
        scenarios=FakeScenarios(),
        instrument_check=(lambda *_: ([], [])) if cell == "X1" else None,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return make_repo(tmp_path)


def batch(
    repo: Repo,
    name: str,
    trials: Sequence[Sequence[tuple[str, str]] | None] | None = None,
    *,
    cell: str = "R1",
    **kwargs: object,
) -> Path:
    auth = authorized(repo, cell)
    manifest = manifest_for(auth.run_commit, **kwargs.pop("manifest", {}))  # type: ignore[arg-type]
    rows = trials if trials is not None else [R1_AS_PREDICTED] * N
    return make_batch(repo.root.parent / "runs", name, auth, manifest, rows, **kwargs)  # type: ignore[arg-type]


# -- verification --------------------------------------------------------------------------------


def test_a_batch_run_as_preregistered_verifies(repo: Repo) -> None:
    verified = verify_batch(batch(repo, "b1"), repo.root)
    assert verified.authorization.cell == "R1" and len(verified.trials) == N


def problems(repo: Repo, directory: Path) -> str:
    with pytest.raises(BatchNotVerified) as caught:
        verify_batch(directory, repo.root)
    return "\n".join(caught.value.problems)


def test_a_preregistration_edited_before_the_run_breaks_the_proof(repo: Repo) -> None:
    d = batch(repo, "b1")
    edited = (repo.root / PREREGISTRATION_PATH).read_text(encoding="utf-8") + "\nedit\n"
    new_head = commit(repo.root, {str(PREREGISTRATION_PATH): edited})
    auth = json.loads((d / "authorization.json").read_text(encoding="utf-8"))
    auth["run_commit"] = new_head
    (d / "authorization.json").write_text(json.dumps(auth), encoding="utf-8")
    (d / "manifest.json").write_bytes(manifest_for(new_head).to_json().encode())
    found = problems(repo, d)
    assert "the pre-registration changed between its commit and the run" in found


def test_a_run_from_before_the_preregistration_breaks_the_proof(repo: Repo) -> None:
    d = batch(repo, "b1")
    auth = json.loads((d / "authorization.json").read_text(encoding="utf-8"))
    auth["run_commit"] = repo.freeze_commit
    (d / "authorization.json").write_text(json.dumps(auth), encoding="utf-8")
    found = problems(repo, d)
    assert "is not an ancestor of the run" in found


def test_a_trial_classified_by_another_checker_is_refused(repo: Repo) -> None:
    d = batch(repo, "b1", checker_changes={"frozen_sha256": "9" * 64})
    assert "checker.json frozen_sha256 is '9999" in problems(repo, d)
    d2 = batch(repo, "b2", checker_changes={"authorized_for_new_evidence": False})
    assert "authorized_for_new_evidence is False" in problems(repo, d2)


def test_a_batch_must_hold_exactly_n_trials(repo: Repo) -> None:
    d = batch(repo, "b1", [R1_AS_PREDICTED] * (N - 1))
    assert f"are not exactly 1..{N}" in problems(repo, d)


def test_the_environment_must_be_the_preregistered_one(repo: Repo) -> None:
    d = batch(repo, "b1", manifest={"os_system": "Windows"})
    assert "manifest os_system is 'Windows'" in problems(repo, d)


# -- aggregation ---------------------------------------------------------------------------------


def test_counts_intervals_and_the_prediction_check(repo: Repo) -> None:
    report = aggregate([batch(repo, "b1"), batch(repo, "b2")], repo.root)
    [cell] = report.cells
    assert cell.pooled and len(cell.groups) == 1
    group = cell.groups[0]
    assert group.trials == 2 * N and group.invalid == 0 and group.status == "as-predicted"
    i4 = next(i for i in group.invariants if i.invariant == "I4")
    assert (i4.exercised, i4.anomalies, i4.values) == (2 * N, 2 * N, {"EXPECTED": 2 * N})
    assert i4.wilson_95 is not None and i4.wilson_95[1] == 1.0
    assert i4.sentence is not None and i4.sentence.startswith(f"{2 * N} of {2 * N} trials")
    i1b = next(i for i in group.invariants if i.invariant == "I1b")
    assert (i1b.anomalies, i1b.rule_of_three_95) == (0, round(3 / (2 * N), 4))


def test_a_different_runner_is_never_pooled_silently(repo: Repo) -> None:
    a = batch(repo, "b1")
    b = batch(repo, "b2", manifest={"os_release": "6.11.0"})
    report = aggregate([a, b], repo.root)
    [cell] = report.cells
    assert not cell.pooled and len(cell.groups) == 2
    assert cell.not_pooled_because == {"os_release": ["'6.11.0'", "'6.8.0'"]}
    rule = CompatibilityRule(field="os_release", values=("6.8.0", "6.11.0"), reason="kernel bump")
    assert aggregate([a, b], repo.root, [rule]).cells[0].pooled


def test_one_unverified_batch_refuses_the_whole_summary(repo: Repo) -> None:
    good = batch(repo, "b1")
    bad = batch(repo, "b2", checker_changes={"checker_sha256": "9" * 64})
    with pytest.raises(AggregationRefused, match="b2"):
        aggregate([good, bad], repo.root)


@pytest.mark.parametrize(
    ("rows", "status"),
    [
        ([R1_AS_PREDICTED, R1_AS_PREDICTED, [("I4", "GAP"), ("I1b", "PASS")]], "unstable"),
        ([[("I4", "GAP"), ("I1b", "PASS")]] * N, "differs"),
        ([[("I4", "VIOLATION_CANDIDATE"), ("I1b", "PASS")]] * N, "escalate"),
    ],
)
def test_statuses(repo: Repo, rows: list[list[tuple[str, str]]], status: str) -> None:
    [cell] = aggregate([batch(repo, "b1", rows)], repo.root).cells
    assert cell.groups[0].status == status


def test_invalid_trials_are_counted_and_exclude_a_cell_above_the_threshold(repo: Repo) -> None:
    d = batch(repo, "b1", invalid={1: "fault not fired"})
    group = aggregate([d], repo.root).cells[0].groups[0]
    assert (group.invalid, group.invalid_reasons) == (1, {"fault not fired": 1})
    assert group.excluded and group.status == "excluded"


def test_a_trial_with_nothing_exercised_counts_as_not_exercised(repo: Repo) -> None:
    rows: list[list[tuple[str, str]] | None] = [[("I5a", "GAP")], [("I5a", "GAP")], None]
    group = aggregate([batch(repo, "x", rows, cell="X1")], repo.root).cells[0].groups[0]
    i5a = group.invariants[0]
    assert i5a.values == {"GAP": 2, "NOT_EXERCISED": 1}
    assert (i5a.exercised, i5a.status) == (2, "unstable")


def test_the_gate_reads_the_instrument_results(repo: Repo) -> None:
    check = instrument_ready(repo.root)
    prereg = prereg_model()
    missing, _ = check(prereg, repo.prereg_commit, [])
    assert missing == ["instrument cell R1 has no batch"]
    missing, used = check(prereg, repo.prereg_commit, [batch(repo, "inst")])
    assert (missing, used) == ([], ["inst"])
    bad = batch(repo, "inst2", [[("I4", "GAP"), ("I1b", "PASS")]] * N)
    missing, _ = check(prereg, repo.prereg_commit, [bad])
    assert missing == ["instrument cell R1 is differs, not as predicted"]


def test_cli_verify_and_aggregate(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    d = batch(repo, "b1")
    out = repo.root.parent / "cells.json"
    assert main(["experiment", "verify", str(d), "--root", str(repo.root)]) == 0
    assert "verified: cell R1" in capsys.readouterr().out
    args = ["experiment", "aggregate", str(d), "--root", str(repo.root), "--out", str(out)]
    assert main(args) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["cells"][0]["cell"] == "R1"
