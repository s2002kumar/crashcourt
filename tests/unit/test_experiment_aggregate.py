from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from crashcourt.cli import main
from crashcourt.experiment.aggregate import AggregationRefused, aggregate, instrument_ready
from crashcourt.experiment.fingerprint import CompatibilityRule, fingerprint
from crashcourt.experiment.gate import Authorization, InstrumentEvidence
from crashcourt.experiment.preregistration import (
    BUILDER_PATH,
    PREREGISTRATION_PATH,
    Preregistration,
)
from crashcourt.experiment.verify import BatchNotVerified, verify_batch

from ._experiment import (
    BUILDER_SHA,
    BUILDER_TEXT,
    HOST,
    LEDGER,
    SEEDS,
    FakeChecker,
    FakeScenarios,
    N,
    Repo,
    commit,
    git,
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
        instrument_check=(
            lambda *_: InstrumentEvidence(
                [],
                ["inst"],
                fingerprint(manifest_for(git(repo.root, "rev-parse", "HEAD")), HOST),
            )
        )
        if cell == "X1"
        else None,
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


def test_an_instrument_batch_authorized_under_a_draft_verifies(tmp_path: Path) -> None:
    draft = make_repo(tmp_path, prereg_model(state="draft"))
    verified = verify_batch(batch(draft, "b1"), draft.root)
    assert verified.authorization.preregistration_state == "draft"


def test_verification_applies_the_draft_rule_again(tmp_path: Path) -> None:
    draft = make_repo(tmp_path, prereg_model(state="draft"))
    d = batch(draft, "b1")
    auth = json.loads((d / "authorization.json").read_text(encoding="utf-8"))
    auth["cell"] = "X1"
    (d / "authorization.json").write_text(json.dumps(auth), encoding="utf-8")
    assert (
        "at the pre-registration commit, the pre-registration is a draft; new-evidence cell X1 "
        "needs it final" in problems(draft, d)
    )


def test_the_recorded_state_must_be_the_preregistrations(repo: Repo) -> None:
    d = batch(repo, "b1")
    auth = json.loads((d / "authorization.json").read_text(encoding="utf-8"))
    auth["preregistration_state"] = "draft"
    (d / "authorization.json").write_text(json.dumps(auth), encoding="utf-8")
    assert "the authorization records a draft pre-registration; it was final at its commit" in (
        problems(repo, d)
    )


def test_the_scenario_run_must_declare_the_cells_workers(repo: Repo) -> None:
    one = batch(repo, "b1", scenario_text='{"cell":"R1","seed":"seed-r1","workers":["w1"]}')
    assert "scenario.json declares 1 worker, pre-registered 2" in problems(repo, one)
    none = batch(repo, "b2", scenario_text='{"cell":"R1","seed":"seed-r1"}')
    assert "scenario.json declares no workers, pre-registered 2" in problems(repo, none)


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


def test_runners_of_one_label_but_another_size_are_never_pooled_silently(repo: Repo) -> None:
    """One GitHub-hosted label gives a public repository 4 CPUs and 16 GB and a private one 2 and
    8, and the manifest names neither; run.json's host does, and no two of these pool."""
    public = batch(repo, "b1")
    private = batch(repo, "b2", run_changes={"host": {"cpu_count": 2, "memory_gib": 8}})
    unrecorded = batch(repo, "b3", run_changes={"host": None})
    for other, why in ((private, {"host.cpu_count", "host.memory_gib"}), (unrecorded, None)):
        [cell] = aggregate([public, other], repo.root).cells
        assert not cell.pooled
        assert set(cell.not_pooled_because) == (why or {"host.cpu_count", "host.memory_gib"})
    assert aggregate([public, batch(repo, "b4")], repo.root).cells[0].pooled


def test_a_verified_batch_carries_the_host_its_run_json_records(repo: Repo) -> None:
    verified = verify_batch(batch(repo, "b1"), repo.root)
    assert verified.host == HOST
    assert verified.environment["host.cpu_count"] == 4


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
    assert check(prereg, repo.prereg_commit, []).missing == ["instrument cell R1 has no batch"]
    good = batch(repo, "inst")
    evidence = check(prereg, repo.prereg_commit, [good])
    assert (evidence.missing, evidence.batches) == ([], ["inst"])
    assert evidence.fingerprint is not None and evidence.fingerprint["runner"] == "local"
    bad = batch(repo, "inst2", [[("I4", "GAP"), ("I1b", "PASS")]] * N)
    assert check(prereg, repo.prereg_commit, [bad]).missing == [
        "instrument cell R1 is differs, not as predicted"
    ]
    elsewhere = batch(repo, "inst3", manifest={"os_release": "6.11.0"})
    assert check(prereg, repo.prereg_commit, [good, elsewhere]).missing == [
        "instrument cell R1 ran in more than one environment",
        "the instrument cells ran in different environments (os_release)",
    ]


def test_cli_verify_and_aggregate(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    d = batch(repo, "b1")
    out = repo.root.parent / "cells.json"
    assert main(["experiment", "verify", str(d), "--root", str(repo.root)]) == 0
    assert "verified: cell R1" in capsys.readouterr().out
    args = ["experiment", "aggregate", str(d), "--root", str(repo.root), "--out", str(out)]
    assert main(args) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["cells"][0]["cell"] == "R1"


def test_the_runner_records_are_recomputed(repo: Repo) -> None:
    assert "scenario.json hashes to" in problems(
        repo, batch(repo, "b1", scenario_text='{"cell":"R1","seed":"other"}')
    )
    assert "run.json mode is 'ordinary', expected 'canonical'" in problems(
        repo, batch(repo, "b2", run_changes={"mode": "ordinary"})
    )
    from crashcourt.experiment.preregistration import derive_seed

    seeds = {str(n): derive_seed(SEEDS["R1"], "R1", n) for n in range(1, N + 1)}
    seeds["2"] = "0" * 64
    found = problems(repo, batch(repo, "b3", run_changes={"trial_seeds": seeds}))
    assert "trial seeds are not the pre-registered derivation (trials ['2'])" in found


def test_the_seed_rule_is_the_one_the_runner_uses() -> None:
    from crashcourt.experiment.preregistration import derive_seed
    from crashcourt.spec.scenario import trial_seed

    for master, cell, trial in (("smoke-v1", "smoke", 1), ("s", "K1a", 20), ("é", "N12b", 7)):
        assert derive_seed(master, cell, trial) == trial_seed(master, cell, trial)


def test_a_freeze_changed_after_the_preregistration_breaks_the_proof(repo: Repo) -> None:
    d = batch(repo, "b1")
    from crashcourt.experiment.gate import FROZEN_PATH

    from ._experiment import FROZEN_MD

    later = commit(repo.root, {str(FROZEN_PATH): FROZEN_MD + "edited\n"}, "edit the freeze")
    auth = json.loads((d / "authorization.json").read_text(encoding="utf-8"))
    auth["run_commit"] = later
    (d / "authorization.json").write_text(json.dumps(auth), encoding="utf-8")
    (d / "manifest.json").write_bytes(manifest_for(later).to_json().encode())
    found = problems(repo, d)
    assert "changed between the freeze and the run" in found


def test_a_trial_without_results_must_say_what_was_not_exercised(repo: Repo) -> None:
    d = batch(repo, "b1", [R1_AS_PREDICTED, R1_AS_PREDICTED, None], checker_changes={})
    # make_batch lists I5a as not exercised for a trial with no results; erase that.
    report = d / "R1" / "trial-00003" / "checker.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    data["not_exercised"] = []
    report.write_text(json.dumps(data), encoding="utf-8")
    assert "no result.json, and checker.json names nothing not exercised" in problems(repo, d)


def test_unstable_and_escalate_are_flags_beside_the_prediction(repo: Repo) -> None:
    rows = [
        [("I4", "EXPECTED"), ("I1b", "PASS")],
        [("I4", "VIOLATION_CANDIDATE"), ("I1b", "PASS")],
        [("I4", "EXPECTED"), ("I1b", "PASS")],
    ]
    group = aggregate([batch(repo, "b1", rows)], repo.root).cells[0].groups[0]
    i4 = next(i for i in group.invariants if i.invariant == "I4")
    assert (i4.unstable, i4.escalate, i4.prediction) == (True, True, "differs")
    assert group.status == "escalate"


def test_the_report_lists_the_rules_it_applied(repo: Repo) -> None:
    a = batch(repo, "b1")
    b = batch(repo, "b2", manifest={"os_release": "6.11.0"})
    rule = CompatibilityRule(field="os_release", values=("6.8.0", "6.11.0"), reason="kernel bump")
    report = aggregate([a, b], repo.root, [rule])
    assert report.compatibility_rules == (rule,)
    assert report.cells[0].pooled and report.cells[0].n == N


def test_the_runner_entry_point_authorizes_new_evidence_from_instrument_batches(
    repo: Repo,
) -> None:
    from crashcourt.experiment.aggregate import authorize
    from crashcourt.experiment.gate import NotAuthorized

    x1 = repo.root / "scenarios/X1.yaml"
    with pytest.raises(NotAuthorized, match="instrument cell R1 has no batch"):
        authorize(x1, N, repo.root, checker=FakeChecker(), scenarios=FakeScenarios())
    inst = batch(repo, "inst")
    auth = authorize(x1, N, repo.root, [inst], checker=FakeChecker(), scenarios=FakeScenarios())
    assert auth.instrument_batches == ("inst",)
    assert auth.instrument_fingerprint is not None
    assert auth.instrument_fingerprint["git_sha"] == auth.run_commit


def test_a_revoked_batch_is_refused_with_the_runners_reasons(repo: Repo) -> None:
    d = batch(repo, "b1")
    auth = json.loads((d / "authorization.json").read_text(encoding="utf-8"))
    (d / "authorization.json").unlink()
    auth["revoked"] = ["HEAD moved during the batch"]
    (d / "authorization.revoked.json").write_text(json.dumps(auth), encoding="utf-8")
    assert "the runner revoked this batch's authorization: HEAD moved during the batch" in (
        problems(repo, d)
    )
    with pytest.raises(AggregationRefused, match="revoked"):
        aggregate([d], repo.root)


# -- observations --------------------------------------------------------------------------------


def r1_prereg(**changes: Any) -> Preregistration:
    data = prereg_model().model_dump(mode="json")
    data["cells"][0].update(changes)
    return Preregistration.model_validate(data)


def test_a_replication_is_scored_on_its_observation_when_the_outcome_is_gap(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path, r1_prereg(predicted=[{"invariant": "I4", "expect": ["GAP"]}]))
    gap = [[("I4", "GAP")]] * N
    group = aggregate([batch(repo, "b1", gap)], repo.root).cells[0].groups[0]
    [seen] = group.observations
    assert (seen.observation, seen.trials, seen.matched, seen.mismatches) == (
        "ledger charge: 2 rows, attempts [1, 2]",
        N,
        N,
        {},
    )
    assert group.status == "as-predicted"
    one_row = {"rows": LEDGER["rows"][:1], "verify_ok": True}
    shown = batch(repo, "b2", gap, ledgers=dict.fromkeys(range(1, N + 1), one_row))
    group = aggregate([shown], repo.root).cells[0].groups[0]
    [seen] = group.observations
    assert (seen.matched, seen.prediction) == (0, "differs")
    assert seen.mismatches["b2 trial 1"] == "1 charge row, attempts [1]"
    assert group.invariants[0].status == "as-predicted"  # the outcome alone matched
    assert group.status == "differs"
    check = instrument_ready(repo.root)
    assert check(
        r1_prereg(predicted=[{"invariant": "I4", "expect": ["GAP"]}]), repo.prereg_commit, [shown]
    ).missing == ["instrument cell R1 is differs, not as predicted"]


def test_an_accepted_run_with_no_record_is_matched_when_nothing_is_scored(
    tmp_path: Path,
) -> None:
    record = "accepted run with no durable record"
    repo = make_repo(
        tmp_path,
        r1_prereg(
            predicted=[
                {"invariant": "I1a", "expect": ["NOT_EXERCISED"]},
                {"invariant": "I1b", "expect": ["NOT_EXERCISED"]},
            ],
            observations=[{"source": "checker", "starts_with": record, "count": 1}],
        ),
    )
    nothing = {
        "not_exercised": [
            {"invariant": "I1a", "missing": "no acknowledged checkpoint"},
            {"invariant": "I1b", "missing": "no acknowledged checkpoint"},
        ]
    }
    said = {"observations": [f"{record}: op start-1 ended client.info", "w1 seq 4 observed head"]}
    trials: list[list[tuple[str, str]] | None] = [None] * N
    good = batch(repo, "b1", trials, checker_changes={**nothing, **said})
    group = aggregate([good], repo.root).cells[0].groups[0]
    assert [i.values for i in group.invariants] == [{"NOT_EXERCISED": N}] * 2
    assert (group.observations[0].matched, group.status) == (N, "as-predicted")
    silent = batch(repo, "b2", trials, checker_changes=nothing, invalid={3: "uncontrolled trial"})
    group = aggregate([silent], repo.root).cells[0].groups[0]
    [seen] = group.observations
    assert seen.trials == N - 1  # an INVALID trial is counted as INVALID, never matched
    assert seen.mismatches == {
        f"b2 trial {n}": f"0 checker records beginning {record!r}" for n in (1, 2)
    }


def test_a_ledger_the_sink_did_not_verify_matches_nothing(repo: Repo) -> None:
    d = batch(repo, "b1", ledgers={1: {**LEDGER, "verify_ok": False}, 2: None})
    group = aggregate([d], repo.root).cells[0].groups[0]
    [seen] = group.observations
    assert seen.mismatches == {
        "b1 trial 1": "ledger.json is not an export the sink verified",
        "b1 trial 2": "no ledger.json",
    }
    assert seen.unstable and group.status == "unstable"


def test_an_anomaly_is_observed_whatever_its_class(tmp_path: Path) -> None:
    anomaly = {"source": "anomaly", "invariant": "I4", "present": True}
    repo = make_repo(
        tmp_path,
        r1_prereg(
            predicted=[{"invariant": "I4", "expect": ["EXPECTED", "GAP", "VIOLATION_CANDIDATE"]}],
            observations=[anomaly],
            deterministic=False,
        ),
    )
    rows = [[("I4", "EXPECTED")], [("I4", "GAP")], [("I4", "VIOLATION_CANDIDATE")]]
    [seen] = aggregate([batch(repo, "b1", rows)], repo.root).cells[0].groups[0].observations
    assert (seen.observation, seen.matched) == ("anomaly on I4: present", N)
    rows = [[("I4", "PASS")], [("I4", "GAP")], [("I4", "GAP")]]
    [seen] = aggregate([batch(repo, "b2", rows)], repo.root).cells[0].groups[0].observations
    assert seen.mismatches == {"b2 trial 1": "I4 is PASS"}


def test_verification_refuses_a_run_whose_builder_changed(repo: Repo) -> None:
    """A batch claiming a run commit whose builder is not the pre-registered one is refused,
    however it was authorized."""
    auth = authorized(repo)
    changed = commit(repo.root, {BUILDER_PATH: BUILDER_TEXT + "# changed\n"}, "change the builder")
    moved = auth.model_copy(update={"run_commit": changed})
    d = make_batch(
        repo.root.parent / "runs",
        "b1",
        moved,
        manifest_for(changed),
        [R1_AS_PREDICTED] * N,
    )
    found = problems(repo, d)
    assert f"{BUILDER_PATH} at the run commit hashes to " in found
    assert f"not the pre-registered {BUILDER_SHA}" in found
    with pytest.raises(AggregationRefused, match=r"declared\.py at the run commit"):
        aggregate([d], repo.root)
