from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.manifest import (
    PINNED_DISTRIBUTIONS,
    Manifest,
    diff_pinned,
    format_libpq_version,
    libpq_major,
    normalize_dist,
    parse_uv_lock,
    pinned_view,
    validate_manifest,
)

from ._factories import D1, GOOD_PG, make_manifest

REPO = Path(__file__).resolve().parents[2]


def test_complete_manifest_has_no_problems() -> None:
    assert validate_manifest(make_manifest()) == []


def test_missing_package_version_is_flagged() -> None:
    m = make_manifest(packages={"langgraph": "1.2.12"})
    problems = validate_manifest(m)
    assert "package version missing: psycopg" in problems
    assert not any("langgraph:" in p or p.endswith(": langgraph") for p in problems)


def test_image_without_digest_is_flagged() -> None:
    m = make_manifest(images={"postgres": "postgres:18", "toxiproxy": f"x:1@{D1}"})
    assert validate_manifest(m) == ["image not pinned by digest: postgres (postgres:18)"]


def test_track_commit_timestamp_off_is_flagged() -> None:
    m = make_manifest(postgres={**GOOD_PG, "track_commit_timestamp": "off"})
    assert validate_manifest(m) == ["track_commit_timestamp must be on, got off"]


def test_missing_git_sha_and_lock_hash_are_flagged() -> None:
    problems = validate_manifest(make_manifest(git_sha="", lockfile_sha256=""))
    assert "git_sha is missing" in problems
    assert "lockfile_sha256 is missing" in problems


def test_json_round_trip_is_lossless_and_deterministic() -> None:
    m = make_manifest()
    text = m.to_json()
    assert Manifest.from_json(text) == m
    assert text == Manifest.from_json(text).to_json()


def test_pinned_view_ignores_host_details() -> None:
    a = make_manifest()
    b = make_manifest(
        generated_at="2027-01-01T00:00:00+00:00",
        os_system="Windows",
        os_release="11",
        machine="AMD64",
        runner="github-hosted",
        docker_version="28.0.0",
        docker_kernel="6.6.87.2-microsoft-standard-WSL2",
        image_ids={"postgres": "sha256:" + "1" * 64, "toxiproxy": "sha256:" + "2" * 64},
        libpq_version="18.6",
        git_dirty=True,
    )
    assert pinned_view(a) == pinned_view(b)
    assert diff_pinned(a, b) == []


def test_pinned_view_detects_what_matters() -> None:
    a = make_manifest()
    b = make_manifest(packages={**a.packages, "langgraph": "9.9.9"}, python_version="3.13.0")
    diffs = diff_pinned(a, b)
    assert any(d.startswith("packages:") for d in diffs)
    assert any(d.startswith("python_version:") for d in diffs)


def test_pinned_view_compares_the_full_python_version() -> None:
    # A runner's system 3.12.3 must not pass for a pinned 3.12.13.
    local = make_manifest(python_version="3.12.13")
    runner = make_manifest(python_version="3.12.3")
    assert diff_pinned(local, runner) == ["python_version: '3.12.13' != '3.12.3'"]


def test_pinned_view_compares_psycopg_impl_and_libpq_major() -> None:
    a = make_manifest()
    diffs = diff_pinned(a, make_manifest(psycopg_impl="python", libpq_version="17.6"))
    assert any(d.startswith("psycopg_impl:") for d in diffs)
    assert "libpq_major: 18 != 17" in diffs


def test_every_langgraph_and_psycopg_distribution_in_the_lock_is_recorded() -> None:
    locked = parse_uv_lock((REPO / "uv.lock").read_text(encoding="utf-8"))
    on_path = {name for name in locked if name.startswith(("langgraph", "psycopg"))}
    assert on_path, "uv.lock lists no langgraph or psycopg distributions"
    assert on_path <= set(PINNED_DISTRIBUTIONS)


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"runner": "laptop"}, "runner must be one of"),
        ({"runner": "unknown"}, "runner must be one of"),
        ({"docker_kernel": "11"}, "docker_kernel is not a kernel release: 11"),
        ({"docker_kernel": ""}, "docker_kernel is missing"),
        ({"psycopg_impl": "pure"}, "psycopg_impl must be one of"),
        ({"libpq_version": "16.4"}, "libpq 16.4 is older than 17"),
        ({"libpq_version": "180004"}, "libpq_version is not major.minor: 180004"),
        ({"image_ids": {"toxiproxy": "sha256:" + "d" * 64}}, "running image ID missing: postgres"),
        ({"image_ids": {"postgres": "abc", "toxiproxy": "sha256:" + "d" * 64}}, "postgres (abc)"),
    ],
)
def test_validation_flags_bad_host_and_driver_fields(
    changes: dict[str, object], problem: str
) -> None:
    problems = validate_manifest(make_manifest(**changes))
    assert any(problem in p for p in problems), problems


def test_libpq_version_formatting() -> None:
    assert format_libpq_version(180004) == "18.4"
    assert format_libpq_version(170010) == "17.10"
    assert libpq_major("18.4") == 18
    assert libpq_major("18") is None


def test_parse_uv_lock_normalizes_names() -> None:
    text = """
version = 1
[[package]]
name = "LangGraph_Checkpoint.Postgres"
version = "3.1.2"
[[package]]
name = "psycopg"
version = "3.3.4"
"""
    assert parse_uv_lock(text) == {"langgraph-checkpoint-postgres": "3.1.2", "psycopg": "3.3.4"}
    assert normalize_dist("Psycopg_Pool") == "psycopg-pool"
