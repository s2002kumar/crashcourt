"""Source snapshots: hash, cache, and re-check the documents clauses cite.

Only hashes and short quotes live in git; full documents go to a gitignored cache, so the
repository never holds copies of documentation pages.
"""

from __future__ import annotations

import hashlib
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from crashcourt import __version__
from crashcourt.clauses.models import Clause
from crashcourt.clauses.registry import ClauseRegistry

Fetcher = Callable[[str], bytes]

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SPACE = re.compile(r"\s+")
#: Element ids in a rendered docs page: HTML attributes, and the ids its compiled MDX assigns
#: (headings, and explicit anchors such as <a id="pending-writes" />).
_ELEMENT_ID = re.compile(rb'\bid="([^"]+)"|\bid:`([^`]+)`')


def normalize(raw: bytes) -> bytes:
    """The bytes a snapshot hash covers: UTF-8 with LF line endings."""
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def snapshot_sha256(raw: bytes) -> str:
    return hashlib.sha256(normalize(raw)).hexdigest()


def plain_text(markdown: str) -> str:
    """Markdown as a reader sees it, for quote matching: link text kept, markup and wraps gone."""
    text = _LINK.sub(r"\1", markdown)
    text = text.replace("`", "").replace("*", "")
    return _SPACE.sub(" ", text).strip()


def quotes_missing(clause: Clause, raw: bytes) -> list[str]:
    text = plain_text(normalize(raw).decode("utf-8"))
    return [q for q in clause.quotes if plain_text(q) not in text]


def element_ids(page: bytes) -> set[str]:
    """The ids a rendered page defines, which are the fragments a link to it can target.

    Checking the page the site serves avoids guessing how the site turns headings into ids.
    """
    return {(a or b).decode("utf-8", "replace") for a, b in _ELEMENT_ID.findall(page)}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"crashcourt/{__version__}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body: bytes = response.read()
    return body


def cache_name(url: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9.-]+", "_", url.split("://", 1)[-1]).strip("_")
    return f"{stem[:120]}-{hashlib.sha256(url.encode()).hexdigest()[:8]}"


@dataclass(frozen=True)
class DriftReport:
    clause_id: str
    url: str
    expected_sha256: str
    actual_sha256: str | None = None
    missing_quotes: tuple[str, ...] = field(default=())
    missing_anchor: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.actual_sha256 == self.expected_sha256
            and not self.missing_quotes
            and self.missing_anchor is None
        )


def refetch(
    registry: ClauseRegistry, cache_dir: Path, fetcher: Fetcher = fetch
) -> list[DriftReport]:
    """Fetch every cited document once, cache it, and compare it with what each clause recorded.

    Never raises for network or drift problems: they are reported, because drift is a prompt to
    re-read the source, not a verdict.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    fetched: dict[str, bytes | str] = {}

    def get(url: str) -> bytes | str:
        if url not in fetched:
            try:
                body = fetcher(url)
            except Exception as exc:  # noqa: BLE001 - any fetch failure is reported, not raised
                fetched[url] = f"{type(exc).__name__}: {exc}"
            else:
                fetched[url] = body
                (cache_dir / cache_name(url)).write_bytes(body)
        return fetched[url]

    reports: list[DriftReport] = []
    for clause in registry:
        url = clause.fetch_url
        result = get(url)
        if isinstance(result, str):
            reports.append(DriftReport(clause.id, url, clause.snapshot_sha256, error=result))
            continue
        page_url, _, fragment = clause.source_url.partition("#")
        missing_anchor: str | None = None
        if fragment:
            page = get(page_url)  # the page the link targets, which may not be the hashed document
            if isinstance(page, str):
                reports.append(
                    DriftReport(clause.id, url, clause.snapshot_sha256, error=f"{page_url}: {page}")
                )
                continue
            missing_anchor = None if fragment in element_ids(page) else fragment
        try:
            report = DriftReport(
                clause.id,
                url,
                clause.snapshot_sha256,
                actual_sha256=snapshot_sha256(result),
                missing_quotes=tuple(quotes_missing(clause, result)),
                missing_anchor=missing_anchor,
            )
        except UnicodeDecodeError as exc:  # a source that is not UTF-8 is reported, not raised
            report = DriftReport(
                clause.id, url, clause.snapshot_sha256, error=f"not UTF-8 text: {exc}"
            )
        reports.append(report)
    return reports
