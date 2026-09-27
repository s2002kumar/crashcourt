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
    assert report.manifest is not None
    assert report.manifest.postgres["track_commit_timestamp"] == "on"
    assert report.manifest.postgres["server_version"].startswith("18")
