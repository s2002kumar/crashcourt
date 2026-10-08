from __future__ import annotations

import json

from crashcourt.probes import (
    parse_compose_containers,
    parse_compose_ps,
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
            json.dumps({"Service": "toxiproxy", "ID": "", "State": "created"}),
        ]
    )
    assert parse_compose_containers(raw) == {"postgres": "a1b2"}


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
