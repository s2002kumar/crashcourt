"""Labeled demo artifacts for building the evidence explorer before any batch exists (F-02).

`crashcourt fixtures --demo` writes them to site/fixtures/. They are never results: every id
starts with DEMO-, every record says provenance "fixture", and the site's release build refuses
them (site/scripts/release-guard.ts).
"""

from crashcourt.fixtures.demo import DEFAULT_SEED, FixtureDirError, differences, generate, write

__all__ = ["DEFAULT_SEED", "FixtureDirError", "differences", "generate", "write"]
