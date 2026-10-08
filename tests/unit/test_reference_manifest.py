"""The reference manifest CI compares against (C7) must stay current.

Each task that changes a pinned field commits its own reference under research/evidence/<task>/ and
points CI at it; older references stay as the record of their task's environment and schema.
"""

from __future__ import annotations

import re
from pathlib import Path

from crashcourt.manifest import Manifest, validate_manifest

REPO = Path(__file__).resolve().parents[2]


def compared_reference() -> Path:
    workflow = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    paths = re.findall(r"--compare\s+(\S+)", workflow)
    assert len(paths) == 1, f"expected one --compare in ci.yml, found {paths}"
    return REPO / paths[0]


def test_reference_ci_compares_against_is_complete_and_from_a_clean_local_checkout() -> None:
    manifest = Manifest.from_json(compared_reference().read_text(encoding="utf-8"))
    assert validate_manifest(manifest) == []
    assert manifest.git_dirty is False
    assert manifest.runner == "local"
