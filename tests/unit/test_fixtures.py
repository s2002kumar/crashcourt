from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterator
from functools import cache
from itertools import pairwise
from pathlib import Path, PurePosixPath

import pytest

from crashcourt.cli import main
from crashcourt.fixtures import DEFAULT_SEED, FixtureDirError, differences, generate, write
from crashcourt.fixtures.demo import BATCHES, FINDING_REPRO, NOTE
from crashcourt.fixtures.scenario import COMPARE, MATRIX
from crashcourt.history.merge import merge
from crashcourt.manifest import Manifest
from crashcourt.spec.content import content_sha256
from crashcourt.spec.events import Event
from crashcourt.spec.records import OUTCOMES, BatchRecord, ResultRecord

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "site" / "fixtures"

#: Envelope fields that hold identifiers; every one a fixture fills starts with DEMO-.
ID_FIELDS = (
    "batch",
    "cell",
    "op_id",
    "logical_op",
    "effect_key",
    "thread_id",
    "checkpoint_id",
    "parent_checkpoint_id",
    "task_id",
    "fault_id",
)


@cache
def files() -> dict[PurePosixPath, bytes]:
    return generate()


def events_in(path: PurePosixPath) -> list[Event]:
    return [Event.model_validate_json(line) for line in files()[path].decode().splitlines()]


def histories() -> Iterator[PurePosixPath]:
    return (p for p in files() if p.suffix == ".jsonl")


def results() -> Iterator[tuple[PurePosixPath, ResultRecord]]:
    for path, data in files().items():
        if path.name == "result.json":
            yield path, ResultRecord.model_validate_json(data)


def batches() -> dict[str, BatchRecord]:
    return {
        p.parts[0]: BatchRecord.model_validate_json(data)
        for p, data in files().items()
        if p.name == "batch.json"
    }


def trial_events(cell_fragment: str) -> Iterator[list[Event]]:
    for path in histories():
        if path.name == "merged.jsonl" and cell_fragment in path.parts[1]:
            yield events_in(path)


def test_generation_depends_only_on_the_seed() -> None:
    assert generate() == files()
    other = generate(DEFAULT_SEED + 1)
    assert other.keys() == files().keys()
    assert other != files()


def test_every_file_is_a_v1_artifact_the_models_accept() -> None:
    for path, data in files().items():
        assert b"\r" not in data, path
        assert data.endswith(b"\n"), path
        if path.name == "manifest.json":
            Manifest.from_json(data.decode())
        elif path.name == "batch.json":
            BatchRecord.model_validate_json(data)
        elif path.name == "result.json":
            ResultRecord.model_validate_json(data)
        else:
            assert path.suffix == ".jsonl", f"{path} is not a v1 artifact"
            assert events_in(path), path


def test_every_record_is_a_fixture_and_names_its_batchs_manifest() -> None:
    records = batches()
    assert sorted(records) == sorted(b.batch for b in BATCHES)
    for name, record in records.items():
        assert record.provenance == "fixture"
        manifest = files()[PurePosixPath(name, "manifest.json")]
        assert record.manifest_sha256 == hashlib.sha256(manifest).hexdigest()
    for path, result in results():
        assert result.provenance == "fixture"
        assert result.batch == path.parts[0]
        assert result.manifest_sha256 == records[result.batch].manifest_sha256
        assert (result.cell, str(result.trial)) == (path.parts[1], path.parts[2])
        for history in (p for p in histories() if p.parent == path.parent):
            for e in events_in(history):
                assert (e.batch, e.cell, e.trial) == (result.batch, result.cell, result.trial)


def test_every_identifier_starts_with_demo() -> None:
    for path in histories():
        for e in events_in(path):
            for name in ID_FIELDS:
                value = getattr(e, name)
                assert value is None or value.startswith("DEMO-"), f"{path}: {name}={value}"
    for _, result in results():
        for r in result.results:
            assert r.invariant.startswith("DEMO-")
            assert r.clause is None or r.clause.startswith("DEMO-")
            assert r.repro is None or r.repro.startswith("DEMO-")
            assert all(ref.startswith("DEMO-") for ref in r.evidence)
    for path, data in files().items():
        if path.name == "manifest.json":
            m = Manifest.from_json(data.decode())
            named = [m.git_sha, m.lockfile_sha256, m.clause_registry_sha256, m.spec_sha256]
            named += [*m.images.values(), *m.image_ids.values(), *m.packages]
            assert all(v.startswith("DEMO-") for v in named), path


def test_every_outcome_says_it_is_demo_data_and_none_is_a_violation() -> None:
    seen: set[str] = set()
    for _, result in results():
        for r in result.results:
            seen.add(r.outcome)
            assert r.reason is not None and NOTE in r.reason
            assert r.review is None
    # Every outcome the explorer renders, except VIOLATION: that needs a human review, which a
    # fixture cannot have.
    assert seen == set(OUTCOMES) - {"VIOLATION"}


def test_scenario_hash_is_the_demonstration_scenarios_content_hash() -> None:
    records = batches()
    assert records["DEMO-batch-matrix"].scenario_sha256 == content_sha256(MATRIX)
    for name in ("DEMO-batch-compare-a", "DEMO-batch-compare-b"):
        assert records[name].scenario_sha256 == content_sha256(COMPARE)
    other = {
        p.parts[0]: BatchRecord.model_validate_json(d)
        for p, d in generate(7).items()
        if p.name == "batch.json"
    }
    assert {n: r.scenario_sha256 for n, r in other.items()} == {
        n: r.scenario_sha256 for n, r in records.items()
    }


def test_clocks_have_real_magnitudes_and_one_host_clock() -> None:
    for path in histories():
        events = events_in(path)
        for e in events:
            assert 1_790_000_000_000_000_000 < e.wall_ns < 1_810_000_000_000_000_000
            assert e.mono_ns > 2**53
            assert e.wall_ns - e.mono_ns == events[0].wall_ns - events[0].mono_ns
        if path.name != "merged.jsonl":
            assert all(a.mono_ns < b.mono_ns for a, b in pairwise(events))


def test_merged_history_is_the_mergers_output() -> None:
    for path in histories():
        if path.name != "merged.jsonl":
            continue
        own = [events_in(p) for p in histories() if p.parent == path.parent and p != path]
        assert len(own) >= 2
        assert list(merge(own)) == events_in(path)


def test_histories_carry_every_fault_mark() -> None:
    injected: set[str] = set()
    healed: set[str] = set()
    for path in histories():
        if path.name != "merged.jsonl":
            continue
        for e in events_in(path):
            marks = [str(e.data.get(k)) for k in ("signal", "toxic", "action") if k in e.data]
            if e.kind == "fault.inject":
                injected.update(marks)
            elif e.kind == "fault.heal":
                healed.update(marks)
    assert {"SIGKILL", "SIGSTOP", "timeout", "reset_peer", "latency", "db-restart"} <= injected
    assert "SIGCONT" in healed
    barriers = [
        events
        for events in trial_events("-F")
        if len([e for e in events if e.kind == "fp.release"]) >= 2
    ]
    assert barriers, "no trial releases two workers at a barrier"


def test_forced_fork_has_two_workers_write_children_of_one_checkpoint() -> None:
    trials = list(trial_events("DEMO-C2-"))
    assert len(trials) == 2
    for events in trials:
        hits = [e for e in events if e.kind == "fp.hit"]
        assert {e.proc for e in hits} == {"worker-a", "worker-b"}
        last_release = max(i for i, e in enumerate(events) if e.kind == "fp.release")
        children: dict[str | None, set[str]] = defaultdict(set)
        for e in events[last_release:]:
            if e.kind == "saver.put.return":
                children[e.parent_checkpoint_id].add(e.proc)
        assert {"worker-a", "worker-b"} in children.values()


def test_flagship_trial_has_a_fork_and_an_orphaned_effect() -> None:
    trials = list(trial_events("-W1-F08"))
    assert len(trials) == 3
    for events in trials:
        parent = {
            e.checkpoint_id: e.parent_checkpoint_id for e in events if e.kind == "saver.put.return"
        }
        head = next(e.checkpoint_id for e in reversed(events) if e.kind == "saver.put.return")
        lineage: set[str | None] = set()
        while head is not None:
            lineage.add(head)
            head = parent.get(head)
        effects = [e for e in events if e.kind == "effect.request"]
        assert effects and all(e.checkpoint_id not in lineage for e in effects)
        assert len(set(parent.values())) < len(parent), "no checkpoint has two children"


def test_cells_are_deterministic_or_randomized_by_their_fault_triggers() -> None:
    for path in histories():
        if path.name != "merged.jsonl":
            continue
        triggers = [e.data["trigger"] for e in events_in(path) if e.kind == "fault.inject"]
        timed = [t for t in triggers if isinstance(t, dict) and "offset_ns" in t]
        if path.parts[1].endswith("-rand"):
            assert timed, path
        else:
            assert not timed, path


def test_there_is_one_finding() -> None:
    repros = {r.repro for _, res in results() for r in res.results if r.repro is not None}
    assert repros == {FINDING_REPRO}


def test_compare_batches_share_a_scenario_and_differ_in_runtime() -> None:
    records = batches()
    a, b = records["DEMO-batch-compare-a"], records["DEMO-batch-compare-b"]
    assert a.scenario_sha256 == b.scenario_sha256 != records["DEMO-batch-matrix"].scenario_sha256
    packages = {
        name: set(json.loads(files()[PurePosixPath(name, "manifest.json")])["packages"])
        for name in ("DEMO-batch-compare-a", "DEMO-batch-compare-b")
    }
    assert packages["DEMO-batch-compare-a"] != packages["DEMO-batch-compare-b"]


def test_committed_fixtures_are_current() -> None:
    assert differences(COMMITTED, files()) == [], "regenerate: uv run crashcourt fixtures --demo"


def test_cli_writes_then_checks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "fixtures"
    assert main(["fixtures", "--demo", "--out", str(out)]) == 0
    assert main(["fixtures", "--demo", "--out", str(out), "--check"]) == 0
    first = next(iter(files()))
    out.joinpath(*first.parts).write_bytes(b"{}\n")
    (out / "DEMO-batch-matrix" / "stray.json").write_bytes(b"{}\n")
    capsys.readouterr()
    assert main(["fixtures", "--demo", "--out", str(out), "--check"]) == 1
    report = capsys.readouterr().out
    assert f"{first} differs" in report
    assert "DEMO-batch-matrix/stray.json is not a generated fixture" in report
    assert main(["fixtures", "--demo", "--out", str(out)]) == 0
    assert differences(out, files()) == []


def test_cli_seed_changes_the_output(tmp_path: Path) -> None:
    out = tmp_path / "fixtures"
    assert main(["fixtures", "--demo", "--out", str(out), "--seed", "9"]) == 0
    assert differences(out, generate(9)) == []
    assert main(["fixtures", "--demo", "--out", str(out), "--check"]) == 1


def test_never_writes_into_a_results_directory(tmp_path: Path) -> None:
    results = tmp_path / "results"
    with pytest.raises(FixtureDirError, match="results"):
        write(results, files())
    assert not results.exists()
    assert main(["fixtures", "--demo", "--out", str(results)]) == 1


def test_never_clears_a_directory_holding_other_things(tmp_path: Path) -> None:
    out = tmp_path / "fixtures"
    out.mkdir()
    (out / "batch-2027-01-15").mkdir()
    with pytest.raises(FixtureDirError, match="batch-2027-01-15"):
        write(out, files())
    assert (out / "batch-2027-01-15").is_dir()
