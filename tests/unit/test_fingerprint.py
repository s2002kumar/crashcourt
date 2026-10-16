from __future__ import annotations

from crashcourt.experiment.fingerprint import (
    NOT_MATERIAL,
    CompatibilityRule,
    differences,
    fingerprint,
    fingerprint_sha256,
    host_record,
    mismatched,
    unexplained,
    unexplained_between,
)

from ._factories import make_manifest


def test_every_field_is_material_but_the_three_that_describe_one_run() -> None:
    assert {"generated_at", "git_dirty", "sink_clock_offset_ns"} == NOT_MATERIAL
    print_ = fingerprint(make_manifest())
    assert "generated_at" not in print_ and "git_dirty" not in print_
    assert print_["runner"] == "local"
    assert print_["postgres.fsync"] == "on"
    assert any(k.startswith("packages.") for k in print_)


def test_one_run_detail_does_not_change_the_fingerprint() -> None:
    a = make_manifest()
    b = make_manifest(generated_at="2027-01-01T00:00:00+00:00", sink_clock_offset_ns=12)
    assert fingerprint_sha256(a) == fingerprint_sha256(b)
    assert differences(a, b) == {}


def test_a_material_difference_keeps_batches_apart() -> None:
    a = make_manifest()
    b = make_manifest(runner="github-hosted", postgres={**a.postgres, "server_version": "18.4"})
    found = differences(a, b)
    assert found == {
        "postgres.server_version": (a.postgres["server_version"], "18.4"),
        "runner": ("local", "github-hosted"),
    }
    assert fingerprint_sha256(a) != fingerprint_sha256(b)
    assert unexplained(a, b) == found


def test_the_host_is_material_and_unrecorded_is_null() -> None:
    m = make_manifest()
    public, private = {"cpu_count": 4, "memory_gib": 16}, {"cpu_count": 2, "memory_gib": 8}
    assert (fingerprint(m)["host.cpu_count"], fingerprint(m)["host.memory_gib"]) == (None, None)
    assert fingerprint(m, public)["host.memory_gib"] == 16
    assert unexplained_between(fingerprint(m, public), fingerprint(m, private)) == {
        "host.cpu_count": (4, 2),
        "host.memory_gib": (16, 8),
    }
    assert set(unexplained_between(fingerprint(m, public), fingerprint(m))) == {
        "host.cpu_count",
        "host.memory_gib",
    }
    assert fingerprint_sha256(m, public) != fingerprint_sha256(m, private)


def test_the_host_record_is_read_from_run_json() -> None:
    assert host_record({"host": {"cpu_count": 4, "memory_gib": 16, "other": 1}}) == {
        "cpu_count": 4,
        "memory_gib": 16,
    }
    for run in ({}, {"host": None}, {"host": "4 CPUs"}):
        assert host_record(run) == {"cpu_count": None, "memory_gib": None}


def test_only_a_recorded_rule_naming_both_values_lets_them_pool() -> None:
    a = make_manifest(os_release="6.8.0")
    b = make_manifest(os_release="6.8.1")
    c = make_manifest(os_release="6.11.0")
    rule = CompatibilityRule(
        field="os_release", values=("6.8.0", "6.8.1"), reason="patch release of one runner image"
    )
    assert unexplained(a, b, [rule]) == {}
    assert unexplained(a, c, [rule]) == {"os_release": ("6.8.0", "6.11.0")}


def test_required_environment_values() -> None:
    m = make_manifest()
    assert mismatched(m, {"os_system": "Linux", "git_dirty": False, "postgres.fsync": "on"}) == {}
    assert mismatched(m, {"runner": "github-hosted", "nowhere": 1}) == {
        "nowhere": (1, "<absent>"),
        "runner": ("github-hosted", "local"),
    }
