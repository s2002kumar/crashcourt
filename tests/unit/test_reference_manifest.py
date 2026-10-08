"""Committed reference manifests are what CI compares against (C7); each must stay current."""

from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.manifest import Manifest, validate_manifest

EVIDENCE = Path(__file__).resolve().parents[2] / "research/evidence"
REFERENCES = sorted(EVIDENCE.glob("*/manifest-local.json"))


def test_there_is_a_reference_manifest() -> None:
    assert REFERENCES, f"no manifest-local.json under {EVIDENCE}"


@pytest.mark.parametrize("path", REFERENCES, ids=lambda p: p.parent.name)
def test_reference_manifest_is_complete_and_from_a_clean_local_checkout(path: Path) -> None:
    manifest = Manifest.from_json(path.read_text(encoding="utf-8"))
    assert validate_manifest(manifest) == []
    assert manifest.git_dirty is False
    assert manifest.runner == "local"
