from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashcourt.manifest import (
    PINNED_DISTRIBUTIONS,
    Manifest,
    ManifestFormatError,
    diff_pinned,
    format_libpq_version,
    libpq_major,
    normalize_dist,
    parse_uv_lock,
    pinned_view,
    platform_notes,
    validate_manifest,
)

from ._factories import D1, GOOD_PG, WINDOWS_WHEEL, make_manifest

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
        git_dirty=True,
    )
    assert pinned_view(a) == pinned_view(b)
    assert diff_pinned(a, b) == []
    assert platform_notes(a, b) == []


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


def test_same_wheel_requires_the_same_libpq() -> None:
    a = make_manifest()
    b = make_manifest(libpq_version="18.4", libpq_build_version="18.5")
    assert diff_pinned(a, b) == [
        "libpq_version: '18.6' != '18.4'",
        "libpq_build_version: '18.6' != '18.5'",
    ]


def test_other_platform_wheel_reports_its_libpq_without_failing() -> None:
    # psycopg-binary 3.3.6 bundles libpq 18.6 on Linux and 18.4 on Windows.
    linux = make_manifest()
    windows = make_manifest(
        psycopg_wheel=WINDOWS_WHEEL, libpq_version="18.4", libpq_build_version="18.4"
    )
    assert diff_pinned(linux, windows) == []
    [note] = platform_notes(linux, windows)
    assert "18.6" in note and "18.4" in note and WINDOWS_WHEEL in note


def test_other_platform_wheel_still_needs_the_same_libpq_major() -> None:
    windows = make_manifest(
        psycopg_wheel=WINDOWS_WHEEL, libpq_version="17.9", libpq_build_version="17.9"
    )
    assert diff_pinned(make_manifest(), windows) == [
        "libpq_major: 18 != 17",
        "libpq_build_major: 18 != 17",
    ]


def test_system_libpq_is_compared_in_full_whatever_the_wheel() -> None:
    # The python implementation loads the system's libpq; the wheel does not explain a difference.
    a = make_manifest(psycopg_impl="python", libpq_version="17.2", libpq_build_version="17.2")
    b = make_manifest(
        psycopg_impl="python",
        psycopg_wheel=WINDOWS_WHEEL,
        libpq_version="17.9",
        libpq_build_version="17.9",
    )
    assert diff_pinned(a, b) == [
        "libpq_version: '17.2' != '17.9'",
        "libpq_build_version: '17.2' != '17.9'",
    ]
    assert platform_notes(a, b) == []


def test_every_langgraph_and_psycopg_distribution_in_the_lock_is_recorded() -> None:
    locked = parse_uv_lock((REPO / "uv.lock").read_text(encoding="utf-8"))
    on_path = {name for name in locked if name.startswith(("langgraph", "psycopg"))}
    assert on_path, "uv.lock lists no langgraph or psycopg distributions"
    assert on_path <= set(PINNED_DISTRIBUTIONS)


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"clause_registry_sha256": "abc"}, "clause_registry_sha256 is not a sha256 digest"),
        ({"clause_registry_sha256": ""}, "clause_registry_sha256 is not a sha256 digest: missing"),
        ({"runner": "laptop"}, "runner must be one of"),
        ({"runner": "unknown"}, "runner must be one of"),
        ({"docker_kernel": "11"}, "docker_kernel does not start with a version: 11"),
        ({"docker_kernel": ""}, "docker_kernel is missing"),
        ({"psycopg_impl": "pure"}, "psycopg_impl must be one of"),
        ({"psycopg_wheel": ""}, "psycopg_wheel is missing"),
        ({"libpq_version": "16.4"}, "libpq_version 16.4 is older than 17"),
        ({"libpq_build_version": "16.9"}, "libpq_build_version 16.9 is older than 17"),
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


def test_manifest_without_the_new_fields_is_a_format_error() -> None:
    old = json.loads(make_manifest().to_json())
    for name in ("runner", "psycopg_wheel", "image_ids"):
        del old[name]
    with pytest.raises(ManifestFormatError, match=r"missing \['image_ids', 'psycopg_wheel'"):
        Manifest.from_json(json.dumps(old))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]", "a manifest is a JSON object"),
        ('{"schema_version": 2}', "schema_version 2, expected 1"),
    ],
)
def test_non_manifests_are_format_errors(text: str, message: str) -> None:
    with pytest.raises(ManifestFormatError, match=message):
        Manifest.from_json(text)


def test_unknown_field_is_a_format_error() -> None:
    extra = {**json.loads(make_manifest().to_json()), "kernel": "6.8"}
    with pytest.raises(ManifestFormatError, match=r"unknown \['kernel'\]"):
        Manifest.from_json(json.dumps(extra))


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


def test_psycopg_impl_difference_fails() -> None:
    diffs = diff_pinned(make_manifest(), make_manifest(psycopg_impl="c"))
    assert "psycopg_impl: 'binary' != 'c'" in diffs


def test_other_platform_wheel_needs_the_same_build_major() -> None:
    # A Windows build linked against libpq 17 must not pass for a Linux build linked against 18.
    windows = make_manifest(
        psycopg_wheel=WINDOWS_WHEEL, libpq_version="18.4", libpq_build_version="17.2"
    )
    assert diff_pinned(make_manifest(), windows) == ["libpq_build_major: 18 != 17"]
    [note] = platform_notes(make_manifest(), windows)
    assert note.endswith("majors differ")


def test_empty_wheel_never_excuses_a_libpq_difference() -> None:
    # A reference without wheel tags must not switch on the cross-platform allowance.
    other = make_manifest(psycopg_wheel="", libpq_version="18.4", libpq_build_version="18.4")
    assert "libpq_version: '18.6' != '18.4'" in diff_pinned(make_manifest(), other)
    assert platform_notes(make_manifest(), other) == []


@pytest.mark.parametrize(
    ("changes", "wrong"),
    [
        ({"libpq_version": 18.6}, "libpq_version"),
        ({"packages": None}, "packages"),
        ({"images": {"postgres": 1}}, "images"),
        ({"git_dirty": "no"}, "git_dirty"),
    ],
)
def test_wrong_field_types_are_format_errors(changes: dict[str, object], wrong: str) -> None:
    data = {**json.loads(make_manifest().to_json()), **changes}
    with pytest.raises(ManifestFormatError, match=rf"wrong type: \['{wrong}'\]"):
        Manifest.from_json(json.dumps(data))


@pytest.mark.parametrize("version", [None, True, "1"])
def test_schema_version_must_be_the_integer_one(version: object) -> None:
    data = json.loads(make_manifest().to_json())
    if version is None:
        del data["schema_version"]
    else:
        data["schema_version"] = version
    with pytest.raises(ManifestFormatError, match="schema_version"):
        Manifest.from_json(json.dumps(data))


def test_a_different_clause_registry_fails_the_comparison() -> None:
    diffs = diff_pinned(make_manifest(), make_manifest(clause_registry_sha256="1" * 64))
    assert [d.split(":")[0] for d in diffs] == ["clause_registry_sha256"]
