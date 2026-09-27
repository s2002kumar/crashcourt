"""The committed registry itself: every clause validates and the published listing is current."""

from __future__ import annotations

from pathlib import Path

from crashcourt.clauses import ClauseRegistry, Polarity
from crashcourt.clauses.report import listing_markdown

ROOT = Path(__file__).resolve().parents[2]

EXPECTED = {
    "LG-DUR-SYNC": Polarity.GUARANTEES,
    "LG-DUR-ASYNC": Polarity.PERMITS,
    "LG-DUR-EXIT": Polarity.PERMITS,
    "LG-PW": Polarity.GUARANTEES,
    "LG-DE-RERUN": Polarity.PERMITS,
    "LG-INT-RERUN": Polarity.PERMITS,
    "LG-LATEST": Polarity.DESCRIBES,
    "LGP-DT": Polarity.DISCLAIMS,
    "LG-OWN-8796": Polarity.PROPOSED,
}


def test_registry_holds_the_v1_clauses_with_their_polarities() -> None:
    registry = ClauseRegistry.load(ROOT / "clauses")
    assert {c.id: c.polarity for c in registry} == EXPECTED


def test_every_clause_quotes_its_source() -> None:
    registry = ClauseRegistry.load(ROOT / "clauses")
    assert all(c.quotes for c in registry)


def test_published_listing_is_current() -> None:
    registry = ClauseRegistry.load(ROOT / "clauses")
    listing = (ROOT / "research" / "evidence" / "t-02" / "registry.md").read_bytes().decode()
    assert listing == listing_markdown(registry), (
        "regenerate: uv run crashcourt clauses list --markdown "
        "--output research/evidence/t-02/registry.md"
    )
