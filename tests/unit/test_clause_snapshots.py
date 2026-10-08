from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from crashcourt.clauses import Clause, ClauseRegistry, Polarity, VersionScope
from crashcourt.clauses.report import drift_text
from crashcourt.clauses.snapshot import (
    anchor_missing,
    plain_text,
    quotes_missing,
    refetch,
    snapshot_sha256,
)

PAGE = (
    b"# Interrupts\n\n"
    b"When execution resumes, the runtime restarts the entire node from the beginning"
    b"\xe2\x80\x94it\ndoes not resume from the exact line where "
    b"[`interrupt`](https://ref.example.com/i) was\n"
    b"called. **Side effects** before it run *again*.\n"
)


def make_clause(clause_id: str, url: str, digest: str, quotes: tuple[str, ...] = ()) -> Clause:
    return Clause(
        id=clause_id,
        title="t",
        polarity=Polarity.PERMITS,
        paraphrase="p",
        quotes=quotes,
        source_url=url + "#anchor",
        retrieved=date(2026, 9, 27),
        snapshot_sha256=digest,
        version_scope=VersionScope(package="langgraph", specifier=">=1.2,<1.3"),
        applies_to=("AsyncPostgresSaver",),
    )


def test_plain_text_drops_markdown_formatting_and_wrapping() -> None:
    text = plain_text(PAGE.decode())
    assert "where interrupt was called. Side effects before it run again." in text
    assert "https://ref.example.com" not in text


def test_quotes_match_the_rendered_text() -> None:
    quote = (
        "the runtime restarts the entire node from the beginning—it does not resume from "
        "the exact line where interrupt was called."
    )
    clause = make_clause("LG-A", "https://d.example.com/a.md", "0" * 64, (quote, "not there"))
    assert quotes_missing(clause, PAGE) == ["not there"]


def test_hash_ignores_line_ending_style() -> None:
    assert snapshot_sha256(PAGE) == snapshot_sha256(PAGE.replace(b"\n", b"\r\n"))
    assert snapshot_sha256(PAGE) == hashlib.sha256(PAGE).hexdigest()


def test_refetch_reports_match_drift_and_errors_without_raising(tmp_path: Path) -> None:
    good = snapshot_sha256(PAGE)
    registry = ClauseRegistry(
        [
            make_clause("LG-A", "https://d.example.com/a.md", good, ("restarts the entire node",)),
            make_clause("LG-B", "https://d.example.com/a.md", "1" * 64),
            make_clause("LG-C", "https://d.example.com/gone.md", good),
        ]
    )
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url.endswith("gone.md"):
            raise OSError("404 Not Found")
        return PAGE

    reports = {r.clause_id: r for r in refetch(registry, tmp_path, fetch)}
    assert calls == ["https://d.example.com/a.md", "https://d.example.com/gone.md"]
    assert reports["LG-A"].ok and reports["LG-A"].actual_sha256 == good
    assert not reports["LG-B"].ok and reports["LG-B"].actual_sha256 == good
    assert reports["LG-C"].error == "OSError: 404 Not Found" and not reports["LG-C"].ok
    cached = list(tmp_path.iterdir())
    assert len(cached) == 1 and cached[0].read_bytes() == PAGE


def test_a_source_that_is_not_utf8_is_reported_not_raised(tmp_path: Path) -> None:
    clause = make_clause("LG-A", "https://docs.example.com/a", "0" * 64)
    [report] = refetch(ClauseRegistry([clause]), tmp_path, lambda url: b"\xff\xfe not text")
    assert report.error is not None and report.error.startswith("not UTF-8 text")
    assert not report.ok


def anchored(fragment: str) -> Clause:
    return make_clause("LG-A", "https://docs.example.com/a", "0" * 64).model_copy(
        update={
            "source_url": f"https://docs.example.com/a#{fragment}",
            "snapshot_url": "https://docs.example.com/a.md",
        }
    )


def test_anchor_must_match_a_heading_on_the_page() -> None:
    page = b"# Interrupts\n\n## Rules of interrupts\n\ntext\n\n#### put\\_writes / aput\\_writes\n"
    assert anchor_missing(anchored("rules-of-interrupts"), page) is None
    assert anchor_missing(anchored("put_writes-aput_writes"), page) is None
    assert anchor_missing(anchored("pending-writes"), page) == "pending-writes"


def test_a_missing_anchor_is_reported_as_drift(tmp_path: Path) -> None:
    page = b"# Checkpointers\n\n## Why use checkpointers\n"
    clause = anchored("pending-writes").model_copy(
        update={"snapshot_sha256": snapshot_sha256(page)}
    )
    [report] = refetch(ClauseRegistry([clause]), tmp_path, lambda url: page)
    assert report.missing_anchor == "pending-writes" and not report.ok
    assert "ANCHOR LG-A  #pending-writes matches no heading on the page" in drift_text([report])
