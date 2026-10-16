"""Classifying a batch with the frozen checker: what its trials are declared as, from the batch's
own records; never replacing a verdict; and comparing a second run's bytes with the first's. The
checker and the builder are stand-ins here; the integration test runs the real ones."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from crashcourt.cli import main
from crashcourt.experiment import classify
from crashcourt.experiment.classify import NotClassified, classify_batch
from crashcourt.experiment.gate import AUTHORIZATION_FILE, REVOKED_FILE, Authorization

from ._experiment import (
    FakeChecker,
    FakeScenarios,
    N,
    Repo,
    commit,
    make_batch,
    make_repo,
    manifest_for,
)


@dataclass
class _Output:
    trial: int

    def report_json(self) -> str:
        return json.dumps({"trial": self.trial, "invalid": None}) + "\n"

    def result_json(self) -> str:
        return json.dumps({"results": [{"invariant": "I4", "outcome": "GAP"}]}) + "\n"

    @property
    def report(self) -> Any:
        return SimpleNamespace(invalid=None)


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Stand-ins for the builder and the checker; records what each trial was declared as."""
    declared: list[tuple[Any, ...]] = []

    def facts(batch_dir: Path, trial: int, **kwargs: Any) -> int:
        declared.append(
            (trial, kwargs["evidence"], kwargs["replication_of"], kwargs["matrix_cell"])
        )
        return trial

    monkeypatch.setattr(classify, "trial_scenario", facts)
    monkeypatch.setattr(classify, "load_trial", lambda directory, trial: trial)
    monkeypatch.setattr(classify, "check_trial", lambda trial, *a, **k: _Output(trial))
    monkeypatch.setattr(classify, "ClauseRegistry", SimpleNamespace(load=lambda root: None))
    monkeypatch.setattr(classify, "registry_sha256", lambda root: "e" * 64)
    return declared


def _authorized(repo: Repo) -> Authorization:
    from crashcourt.experiment.gate import authorize_batch

    return authorize_batch(
        repo.root / "scenarios/R1.yaml",
        N,
        repo.root,
        checker=FakeChecker(),
        scenarios=FakeScenarios(),
    )


def _unclassified(repo: Repo, name: str, *, authorized: bool = True) -> Path:
    """A batch as the runner leaves it: no checker output in any trial."""
    auth = _authorized(repo)
    d = make_batch(repo.root.parent / "runs", name, auth, manifest_for(auth.run_commit), [None] * N)
    for output in (*d.rglob("checker.json"), *d.rglob("result.json")):
        output.unlink()
    if not authorized:
        (d / AUTHORIZATION_FILE).unlink()
    return d


def test_a_development_batch_is_classified_as_a_control(
    tmp_path: Path, seen: list[tuple[Any, ...]]
) -> None:
    repo = make_repo(tmp_path)
    d = _unclassified(repo, "dev", authorized=False)
    found = classify_batch(d, repo.root)
    assert seen == [(n, "control", None, None) for n in range(1, N + 1)]
    assert [(c.trial, c.invalid, c.outcomes) for c in found] == [
        (n, None, {"I4": "GAP"}) for n in range(1, N + 1)
    ]
    assert (d / "R1" / "trial-00002" / "checker.json").read_text() == _Output(2).report_json()


def test_an_authorized_batch_is_classified_as_its_cell(
    tmp_path: Path, seen: list[tuple[Any, ...]]
) -> None:
    repo = make_repo(tmp_path)
    classify_batch(_unclassified(repo, "b1"), repo.root)
    assert {s[1:] for s in seen} == {("replication", "langgraph#8039", "R1")}


def test_an_authorized_batch_is_classified_only_from_its_run_commit(
    tmp_path: Path, seen: list[tuple[Any, ...]]
) -> None:
    repo = make_repo(tmp_path)
    d = _unclassified(repo, "b1")
    (repo.root / "notes.txt").write_text("uncommitted\n")
    with pytest.raises(NotClassified, match="clean checkout of its run commit"):
        classify_batch(d, repo.root)
    commit(repo.root, {"notes.txt": "committed\n"})
    with pytest.raises(NotClassified, match="clean checkout of its run commit"):
        classify_batch(d, repo.root)
    assert seen == [] and not list(d.rglob("checker.json"))


def test_a_revoked_batch_is_refused(tmp_path: Path, seen: list[tuple[Any, ...]]) -> None:
    repo = make_repo(tmp_path)
    d = _unclassified(repo, "b1")
    (d / AUTHORIZATION_FILE).rename(d / REVOKED_FILE)
    with pytest.raises(NotClassified, match="revoked"):
        classify_batch(d, repo.root)


def test_a_verdict_is_never_replaced(tmp_path: Path, seen: list[tuple[Any, ...]]) -> None:
    repo = make_repo(tmp_path)
    d = _unclassified(repo, "b1")
    classify_batch(d, repo.root)
    report = d / "R1" / "trial-00001" / "checker.json"
    report.write_text("recorded\n")
    with pytest.raises(NotClassified, match=r"already classified \(trial 1 checker\.json"):
        classify_batch(d, repo.root)
    assert report.read_text() == "recorded\n"


def test_compare_names_every_output_whose_bytes_differ(
    tmp_path: Path, seen: list[tuple[Any, ...]]
) -> None:
    repo = make_repo(tmp_path)
    d = _unclassified(repo, "b1")
    classify_batch(d, repo.root)
    assert [c.differs for c in classify_batch(d, repo.root, compare=True)] == [()] * N
    changed = d / "R1" / "trial-00002" / "result.json"
    changed.write_text("edited\n")
    again = classify_batch(d, repo.root, compare=True)
    assert [c.differs for c in again] == [(), ("result.json",), ()]
    assert changed.read_text() == "edited\n"  # compare writes nothing


def test_every_trial_needs_its_directory(tmp_path: Path, seen: list[tuple[Any, ...]]) -> None:
    repo = make_repo(tmp_path)
    d = _unclassified(repo, "b1")
    shutil.rmtree(d / "R1" / "trial-00003")
    with pytest.raises(NotClassified, match=r"no directory for trials \[3\]"):
        classify_batch(d, repo.root)


def test_cli_classify_and_compare(
    tmp_path: Path, seen: list[tuple[Any, ...]], capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(tmp_path)
    d = _unclassified(repo, "b1")
    root = ["--root", str(repo.root)]
    assert main(["experiment", "classify", str(d), *root]) == 0
    out = capsys.readouterr().out
    assert "trial 1: I4 GAP" in out and f"{N} trials classified, {N} valid" in out
    assert main(["experiment", "classify", "--compare", str(d), *root]) == 0
    assert f"{N} of {N} trials byte-identical" in capsys.readouterr().out
    (d / "R1" / "trial-00001" / "checker.json").write_text("edited\n")
    assert main(["experiment", "classify", "--compare", str(d), *root]) == 1
    assert "trial 1: differs (checker.json)" in capsys.readouterr().out
    assert main(["experiment", "classify", str(d), *root]) == 1
    assert "already classified" in capsys.readouterr().out
