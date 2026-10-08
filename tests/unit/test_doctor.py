from __future__ import annotations

from dataclasses import replace

import pytest

from crashcourt.doctor import format_report, run_doctor

from ._factories import (
    GOOD_DRIVER,
    GOOD_IMAGE_IDS,
    GOOD_PG,
    GOOD_RUNNING,
    GOOD_SINK,
    GOOD_VERSIONS,
    ID_PG,
    ID_TOXI,
    LINUX_WHEEL,
    FakeProbes,
)


def failed(probes: FakeProbes) -> dict[str, str]:
    report = run_doctor(probes)
    assert not report.ok
    assert report.manifest is None
    return {c.name: c.detail for c in report.failures}


def test_healthy_environment_yields_complete_manifest() -> None:
    report = run_doctor(FakeProbes())
    assert report.ok, format_report(report)
    m = report.manifest
    assert m is not None
    assert m.postgres["track_commit_timestamp"] == "on"
    assert m.images["postgres"].startswith("postgres:18@sha256:")
    assert m.image_ids == GOOD_RUNNING
    assert m.docker_kernel == "6.8.0-45-generic"
    assert m.runner == "local"
    assert (m.psycopg_impl, m.psycopg_wheel) == ("binary", LINUX_WHEEL)
    assert (m.libpq_version, m.libpq_build_version) == ("18.6", "18.6")


def test_container_running_another_image_fails() -> None:
    # compose.yaml can name the pinned digest while an older container is still running.
    stale = "sha256:" + "e" * 64
    fails = failed(FakeProbes(running={"postgres": [stale], "toxiproxy": [ID_TOXI]}))
    detail = fails["running images match pins"]
    assert detail == f"postgres: containers run {stale}, pinned ref is {ID_PG}"


def test_one_stale_replica_among_several_fails() -> None:
    stale = "sha256:" + "e" * 64
    fails = failed(FakeProbes(running={"postgres": [ID_PG, stale], "toxiproxy": [ID_TOXI]}))
    assert stale in fails["running images match pins"]


def test_service_without_container_fails_image_check() -> None:
    fails = failed(FakeProbes(running={"postgres": [ID_PG]}))
    assert fails["running images match pins"] == "toxiproxy: no container"


def test_pinned_image_absent_locally_fails() -> None:
    present = {ref: i for ref, i in GOOD_IMAGE_IDS.items() if i == ID_PG}
    fails = failed(FakeProbes(local_ids=present))
    assert "toxiproxy: pinned image" in fails["running images match pins"]


def test_image_probe_error_is_a_failed_check_not_a_crash() -> None:
    fails = failed(FakeProbes(running=ValueError("unexpected docker output")))
    assert fails["running images match pins"] == "ValueError: unexpected docker output"


def test_unreachable_docker_skips_the_docker_probes() -> None:
    probes = FakeProbes(docker=None)
    fails = failed(probes)
    for name in ("images pinned by digest", "compose services up", "running images match pins"):
        assert fails[name] == "skipped: docker daemon not reachable"
    assert probes.calls == []


def test_python_other_than_the_pin_fails() -> None:
    fails = failed(FakeProbes(pin="3.12.13"))
    assert fails["python matches .python-version"] == "running 3.12.7, pinned 3.12.13"


def test_missing_python_version_file_fails() -> None:
    fails = failed(FakeProbes(pin=None))
    assert fails["python matches .python-version"] == ".python-version not found"


def test_unreadable_python_version_file_is_a_failed_check() -> None:
    fails = failed(FakeProbes(pin=PermissionError("denied")))
    assert fails["python matches .python-version"] == "PermissionError: denied"


def test_driver_without_cancel_safe_fails() -> None:
    # Runtime libpq 17 but a wrapper built against 16: psycopg falls back to a blocking cancel.
    driver = replace(GOOD_DRIVER, impl="c", libpq=170002, libpq_build=160009, cancel_safe=False)
    fails = failed(FakeProbes(driver=driver))
    assert fails["psycopg cancel_safe"] == (
        "c implementation, libpq 17.2, built against 16.9; cancel_safe() unavailable"
    )


def test_broken_psycopg_is_a_failed_check_not_a_crash() -> None:
    fails = failed(FakeProbes(driver=ImportError("no pq wrapper available")))
    assert fails["psycopg cancel_safe"] == "ImportError: no pq wrapper available"


def test_unknown_runner_blocks_the_manifest() -> None:
    fails = failed(FakeProbes(runner_env="unknown"))
    assert "runner must be one of" in fails["manifest complete"]


def test_missing_docker_kernel_blocks_the_manifest() -> None:
    fails = failed(FakeProbes(kernel=None))
    assert "docker_kernel is missing" in fails["manifest complete"]


def test_docker_missing_fails_with_remedy() -> None:
    report = run_doctor(FakeProbes(docker=None))
    names = {c.name for c in report.failures}
    assert "docker daemon" in names
    assert all(c.remedy for c in report.failures)


def test_unpinned_image_fails() -> None:
    fails = failed(FakeProbes(images={"postgres": "postgres:18", "toxiproxy": "t:1"}))
    assert "postgres=postgres:18" in fails["images pinned by digest"]


def test_missing_compose_config_fails_without_crashing() -> None:
    fails = failed(FakeProbes(images=None))
    assert "postgres=missing" in fails["images pinned by digest"]
    assert "postgres: no image in compose.yaml" in fails["running images match pins"]


def test_unhealthy_postgres_fails() -> None:
    fails = failed(FakeProbes(states={"postgres": "starting", "toxiproxy": "running"}))
    assert "postgres=starting" in fails["compose services up"]


def test_track_commit_timestamp_off_fails() -> None:
    fails = failed(FakeProbes(pg={**GOOD_PG, "track_commit_timestamp": "off"}))
    assert fails["postgres track_commit_timestamp"] == "track_commit_timestamp=off"


def test_postgres_unreachable_is_a_failed_check_not_a_crash() -> None:
    fails = failed(FakeProbes(pg=ConnectionError("refused")))
    assert fails["postgres reachable"] == "ConnectionError: refused"


def test_toxiproxy_unreachable_fails() -> None:
    fails = failed(FakeProbes(toxiproxy=OSError("no route")))
    assert "no route" in fails["toxiproxy api"]


def test_version_mismatch_lists_each_package() -> None:
    fails = failed(FakeProbes(installed={**GOOD_VERSIONS, "psycopg": "0.0.1", "langgraph": None}))
    detail = fails["installed versions match uv.lock"]
    assert "psycopg: installed 0.0.1" in detail
    assert "langgraph: installed None" in detail


def test_missing_lockfile_fails() -> None:
    assert "uv.lock present" in failed(FakeProbes(locked=None))


def test_not_a_git_checkout_fails() -> None:
    assert "git commit" in failed(FakeProbes(git=(None, False)))


def test_dirty_tree_is_recorded_not_failed() -> None:
    report = run_doctor(FakeProbes(git=("1" * 40, True)))
    assert report.ok
    assert report.manifest is not None and report.manifest.git_dirty is True


def test_every_failure_is_reported_at_once() -> None:
    report = run_doctor(
        FakeProbes(docker=None, toxiproxy=OSError("down"), locked=None, git=(None, False))
    )
    names = {c.name for c in report.failures}
    assert {"docker daemon", "toxiproxy api", "uv.lock present", "git commit"} <= names


def test_format_report_uses_ok_and_fail_not_verdict_words() -> None:
    text = format_report(run_doctor(FakeProbes(docker=None)))
    assert "FAIL" in text and "-> Start Docker." in text
    for verdict in ("PASS", "VIOLATION", "EXPECTED", "INVALID"):
        assert verdict not in text


def test_missing_driver_points_to_uv_sync() -> None:
    report = run_doctor(FakeProbes(pg=ModuleNotFoundError("No module named 'psycopg'")))
    [check] = [c for c in report.failures if c.name == "postgres reachable"]
    assert "uv sync" in check.remedy


def test_unreadable_lockfile_is_a_failed_check_not_a_crash() -> None:
    fails = failed(FakeProbes(locked=ValueError("Invalid statement (at line 3, column 1)")))
    assert fails["uv.lock readable"] == "ValueError: Invalid statement (at line 3, column 1)"
    assert "uv.lock present" not in fails


def test_unhashable_lockfile_blocks_the_manifest() -> None:
    fails = failed(FakeProbes(lock_sha=PermissionError("denied")))
    assert "lockfile_sha256 is missing" in fails["manifest complete"]


def test_invalid_clause_registry_is_a_failed_check() -> None:
    from crashcourt.clauses import RegistryError

    fails = failed(FakeProbes(registry=RegistryError(["a.yaml: bad", "b.yaml: worse"])))
    assert fails["clause registry valid"] == "RegistryError: a.yaml: bad; b.yaml: worse"


def test_manifest_records_the_clause_registry_version() -> None:
    report = run_doctor(FakeProbes(registry=(9, "1" * 64)))
    assert report.manifest is not None
    assert report.manifest.clause_registry_sha256 == "1" * 64


def test_spec_provenance_is_recorded_and_checked_where_the_spec_is_present() -> None:
    from ._factories import SPEC_SHA

    report = run_doctor(FakeProbes(spec=(SPEC_SHA, None)))
    assert report.manifest is not None and report.manifest.spec_sha256 == SPEC_SHA
    assert run_doctor(FakeProbes(spec=(SPEC_SHA, True))).ok
    assert "does not match" in failed(FakeProbes(spec=(SPEC_SHA, False)))["spec provenance"]
    broken = failed(FakeProbes(spec=FileNotFoundError("spec-provenance.json")))
    assert broken["spec provenance"] == "FileNotFoundError: spec-provenance.json"


def test_unreachable_sink_fails() -> None:
    fails = failed(FakeProbes(sink=ConnectionError("refused")))
    assert fails["effect sink"] == "ConnectionError: refused"


@pytest.mark.parametrize(
    ("health", "problem"),
    [
        ({**GOOD_SINK, "test_hooks": True}, "a test hook is armed"),
        ({**GOOD_SINK, "ledger_version": 2}, "ledger version 2, expected 1"),
        ({**GOOD_SINK, "status": "degraded"}, "status degraded"),
        ({**GOOD_SINK, "source_sha256": "a" * 64}, "serving source aaaaaaaaaaaa, the checkout is"),
        ({k: v for k, v in GOOD_SINK.items() if k != "source_sha256"}, "serving source None"),
    ],
)
def test_sink_health_problems_fail(health: dict[str, object], problem: str) -> None:
    assert problem in failed(FakeProbes(sink=health))["effect sink"]


def test_unhealthy_sink_container_fails() -> None:
    states = {"postgres": "healthy", "toxiproxy": "running", "sink": "starting"}
    assert "sink=starting" in failed(FakeProbes(states=states))["compose services up"]


def test_unpinned_or_missing_harness_base_fails() -> None:
    fails = failed(FakeProbes(bases=["ghcr.io/astral-sh/uv:0.12.1"]))
    assert "Dockerfile.harness FROM ghcr.io/astral-sh/uv:0.12.1" in fails["images pinned by digest"]
    assert "Dockerfile.harness" in failed(FakeProbes(bases=None))["images pinned by digest"]
