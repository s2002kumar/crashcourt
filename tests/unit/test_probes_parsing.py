from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from crashcourt.probes import (
    SystemProbes,
    parse_compose_containers,
    parse_compose_ps,
    parse_wheel_tags,
    read_python_pin,
    runner_kind,
)


def test_parses_ndjson_and_prefers_health_over_state() -> None:
    raw = "\n".join(
        [
            json.dumps({"Service": "postgres", "State": "running", "Health": "healthy"}),
            json.dumps({"Service": "toxiproxy", "State": "running", "Health": ""}),
        ]
    )
    assert parse_compose_ps(raw) == {"postgres": "healthy", "toxiproxy": "running"}


def test_parses_json_array_shape() -> None:
    raw = json.dumps([{"Service": "postgres", "State": "exited", "Health": ""}])
    assert parse_compose_ps(raw) == {"postgres": "exited"}


def test_empty_output_means_no_services() -> None:
    assert parse_compose_ps("") == {}
    assert parse_compose_ps("  \n") == {}


def test_parses_container_ids_per_service() -> None:
    raw = "\n".join(
        [
            json.dumps({"Service": "postgres", "ID": "a1b2", "State": "running"}),
            json.dumps({"Service": "postgres", "ID": "c3d4", "State": "running"}),
            json.dumps({"Service": "toxiproxy", "ID": "", "State": "created"}),
        ]
    )
    assert parse_compose_containers(raw) == {"postgres": ["a1b2", "c3d4"]}


def test_unreadable_compose_output_means_no_services() -> None:
    assert parse_compose_ps("Error response from daemon: 500") == {}
    assert parse_compose_containers("{not json") == {}


def test_wheel_tags_come_from_the_tag_lines() -> None:
    wheel = (
        "Wheel-Version: 1.0\nGenerator: setuptools (83.0.0)\nRoot-Is-Purelib: false\n"
        "Tag: cp312-cp312-manylinux2014_x86_64\nTag: cp312-cp312-manylinux_2_17_x86_64\n\n"
    )
    assert parse_wheel_tags(wheel) == [
        "cp312-cp312-manylinux2014_x86_64",
        "cp312-cp312-manylinux_2_17_x86_64",
    ]
    assert parse_wheel_tags("") == []


def test_python_pin_skips_comments_and_blank_lines() -> None:
    assert read_python_pin("# pinned for the manifest\n\n3.12.13\n") == "3.12.13"
    assert read_python_pin("") is None


def test_runner_kind_reads_github_actions_variables() -> None:
    assert runner_kind({}) == "local"
    assert runner_kind({"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}) == (
        "github-hosted"
    )
    assert runner_kind({"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}) == (
        "self-hosted"
    )
    assert runner_kind({"GITHUB_ACTIONS": "true"}) == "unknown"
    assert runner_kind({"CI": "true"}) == "unknown"  # another CI system is not a local run
    assert runner_kind({"TF_BUILD": "True"}) == "unknown"  # Azure Pipelines does not set CI


def test_run_survives_output_that_is_not_utf8(tmp_path: Path) -> None:
    probes = SystemProbes(tmp_path)
    out = probes._run(  # pyright: ignore[reportPrivateUsage]
        sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\x81ok')"
    )
    assert out == "\ufffdok"


def fake_run(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str | None]) -> SystemProbes:
    """SystemProbes whose commands answer by their first distinctive word."""
    probes = SystemProbes(Path.cwd())

    def run(*args: str) -> str | None:
        key = "inspect" if "inspect" in args else " ".join(args[1:3])
        return answers.get(key)

    monkeypatch.setattr(probes, "_run", run)
    return probes


def test_compose_config_without_services_reads_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert fake_run(monkeypatch, {"compose config": '{"services": null}'}).compose_images() is None
    odd = fake_run(monkeypatch, {"compose config": '{"services": {"postgres": "x"}}'})
    assert odd.compose_images() == {"postgres": ""}


def test_a_container_that_cannot_be_inspected_stays_in_the_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ps = json.dumps({"Service": "postgres", "ID": "a1b2", "State": "running"})
    probes = fake_run(monkeypatch, {"compose ps": ps, "inspect": None})
    assert probes.running_image_ids() == {"postgres": ["uninspectable container a1b2"]}
