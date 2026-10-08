"""The reference manifest CI compares against (C7) must stay current.

Each task that changes a pinned field commits its own reference under research/evidence/<task>/ and
points CI at it; older references stay as the record of their task's environment and schema.
"""

from __future__ import annotations

import re
from pathlib import Path

from crashcourt.clauses import registry_sha256
from crashcourt.manifest import Manifest, sha256_file, validate_manifest

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


def test_reference_matches_the_lockfile_and_clause_registry_in_the_repository() -> None:
    # CI's comparison fails on either difference; this says so locally, and says what to do.
    manifest = Manifest.from_json(compared_reference().read_text(encoding="utf-8"))
    rerecord = (
        f"re-record {compared_reference().relative_to(REPO).as_posix()} with crashcourt doctor"
    )
    assert manifest.lockfile_sha256 == sha256_file(REPO / "uv.lock"), rerecord
    assert manifest.clause_registry_sha256 == registry_sha256(REPO / "clauses"), rerecord
