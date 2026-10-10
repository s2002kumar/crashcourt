"""The committed registry itself: every clause validates and the published listing is current."""

from __future__ import annotations

from pathlib import Path

from crashcourt.clauses import ClauseRegistry, Polarity, registry_sha256
from crashcourt.clauses.report import listing_markdown

ROOT = Path(__file__).resolve().parents[2]

EXPECTED = {
    "LG-DUR-SYNC": Polarity.GUARANTEES,
    "LG-DUR-ASYNC": Polarity.PERMITS,
    "LG-DUR-EXIT": Polarity.PERMITS,
    "LG-PW-EXC": Polarity.GUARANTEES,
    "LG-PW-CRASH": Polarity.DESCRIBES,
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


#: The savers this lab tests. A proposal is not adopted, so it must never judge them.
OFFICIAL_SAVERS = {"AsyncPostgresSaver", "PostgresSaver", "AsyncSqliteSaver", "SqliteSaver"}


def test_no_proposed_clause_applies_to_an_official_saver() -> None:
    registry = ClauseRegistry.load(ROOT / "clauses")
    proposed = registry.by_polarity(Polarity.PROPOSED)
    assert proposed, "expected at least one proposed clause (LG-OWN-8796)"
    for clause in proposed:
        assert not OFFICIAL_SAVERS & set(clause.applies_to), clause.id


def test_registry_has_a_version() -> None:
    digest = registry_sha256(ROOT / "clauses")
    assert len(digest) == 64 and digest == registry_sha256(ROOT / "clauses")
