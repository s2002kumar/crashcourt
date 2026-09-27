from __future__ import annotations

from crashcourt.doctor import format_report, run_doctor

from ._factories import GOOD_PG, GOOD_VERSIONS, FakeProbes


def failed(probes: FakeProbes) -> dict[str, str]:
    report = run_doctor(probes)
    assert not report.ok
    assert report.manifest is None
    return {c.name: c.detail for c in report.failures}


def test_healthy_environment_yields_complete_manifest() -> None:
    report = run_doctor(FakeProbes())
    assert report.ok, format_report(report)
    assert report.manifest is not None
    assert report.manifest.postgres["track_commit_timestamp"] == "on"
    assert report.manifest.images["postgres"].startswith("postgres:18@sha256:")


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
