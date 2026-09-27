from __future__ import annotations

from crashcourt.manifest import (
    Manifest,
    diff_pinned,
    normalize_dist,
    parse_uv_lock,
    pinned_view,
    validate_manifest,
)

from ._factories import D1, GOOD_PG, make_manifest


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
        os_system="Darwin",
        machine="arm64",
        python_version="3.12.9",
        docker_version="28.0.0",
        git_dirty=True,
    )
    assert pinned_view(a) == pinned_view(b)
    assert diff_pinned(a, b) == []


def test_pinned_view_detects_what_matters() -> None:
    a = make_manifest()
    b = make_manifest(packages={**a.packages, "langgraph": "9.9.9"}, python_version="3.13.0")
    diffs = diff_pinned(a, b)
    assert any(d.startswith("packages:") for d in diffs)
    assert any(d.startswith("python_minor:") for d in diffs)


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
