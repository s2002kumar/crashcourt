"""Runs against the real compose services.

Usage: docker compose up -d --wait && uv run pytest -m integration
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.doctor import format_report, run_doctor
from crashcourt.probes import SystemProbes

pytestmark = pytest.mark.integration


def test_doctor_passes_against_compose() -> None:
    report = run_doctor(SystemProbes(Path.cwd()))
    assert report.ok, format_report(report)
    manifest = report.manifest
    assert manifest is not None
    assert manifest.postgres["track_commit_timestamp"] == "on"
    assert manifest.postgres["server_version"].startswith("18")
    assert manifest.python_version == Path(".python-version").read_text(encoding="utf-8").strip()
    assert set(manifest.image_ids) == {"postgres", "toxiproxy"}
