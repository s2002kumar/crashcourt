"""The committed reference manifest is what CI compares against (C7); it must stay current."""

from __future__ import annotations

from pathlib import Path

from crashcourt.manifest import Manifest, validate_manifest

REFERENCE = Path(__file__).resolve().parents[2] / "research/evidence/t-01/manifest-local.json"


def test_reference_manifest_is_complete_and_from_a_clean_local_checkout() -> None:
    manifest = Manifest.from_json(REFERENCE.read_text(encoding="utf-8"))
    assert validate_manifest(manifest) == []
    assert manifest.git_dirty is False
    assert manifest.runner == "local"
