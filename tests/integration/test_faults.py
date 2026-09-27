"""Every fault primitive against the compose services, once (CI also runs the 20-trial self-test).

Signal faults need POSIX; on Linux (CI) the signal check must pass, not skip.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from crashcourt.faults import selftest
from crashcourt.probes import DEFAULT_PG_DSN, DEFAULT_PG_PROXY_DSN, DEFAULT_TOXIPROXY_URL

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def test_one_selftest_trial_passes_every_check(tmp_path: Path) -> None:
    targets = selftest.prepare(
        os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN),
        os.environ.get("CRASHCOURT_PG_PROXY_DSN", DEFAULT_PG_PROXY_DSN),
        os.environ.get("CRASHCOURT_TOXIPROXY_URL", DEFAULT_TOXIPROXY_URL),
        ROOT,
    )
    try:
        trial = selftest.run_trial(targets, 1, tmp_path, restart=True)
    finally:
        targets.toxiproxy.reset()
    statuses = {c.name: c.status for c in trial.checks}
    failed = {c.name: c.observed for c in trial.checks if c.status == "fail"}
    assert not failed, failed
    expected_skips = {"signals"} if sys.platform == "win32" else set()
    assert {name for name, status in statuses.items() if status == "skip"} == expected_skips
