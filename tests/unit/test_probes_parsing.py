from __future__ import annotations

import json

from crashcourt.probes import parse_compose_ps


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
