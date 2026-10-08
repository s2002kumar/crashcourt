"""Clause model: one documented statement a verdict may cite (control C6)."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)


class Polarity(StrEnum):
    """What a clause does for a verdict.

    guarantees: a broken clause is a VIOLATION candidate; a clause that holds is PASS.
    permits: an anomaly it covers is EXPECTED.
    describes: context for invariants. Open question, to settle with the checker: whether a
        describes clause can support a verdict on its own. LG-LATEST is the only clause behind the
        lineage and head invariants, so the answer decides whether those can ever be violations.
    disclaims: the runtime explicitly does not provide the behavior.
    proposed: an upstream proposal; judges only implementations that advertise it.
    """

    GUARANTEES = "guarantees"
    PERMITS = "permits"
    DESCRIBES = "describes"
    DISCLAIMS = "disclaims"
    PROPOSED = "proposed"


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date(value: object) -> date:
    """Accept a date or a YYYY-MM-DD string; reject timestamps, integers, and other formats."""
    if isinstance(value, datetime):
        raise ValueError("must be a date (YYYY-MM-DD), not a timestamp")
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        return date.fromisoformat(value)
    raise ValueError("must be a date in YYYY-MM-DD form")


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://[^\s#]+(#\S*)?$")]
IsoDate = Annotated[date, BeforeValidator(_iso_date)]


class VersionScope(BaseModel):
    """The package versions a clause was read for; verdicts outside the scope cannot cite it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    specifier: Text

    @field_validator("specifier")
    @classmethod
    def _valid_specifier(cls, value: str) -> str:
        try:
            SpecifierSet(value)
        except InvalidSpecifier as exc:
            raise ValueError(f"not a PEP 440 version specifier: {value!r}") from exc
        return value

    def contains(self, version: str) -> bool:
        return Version(version) in SpecifierSet(self.specifier)


class Clause(BaseModel):
    """A version-pinned, polarity-tagged statement from a primary source.

    `paraphrase` must never be stronger than the source. `quotes` hold exact source sentences
    (as rendered, without markup) so a refetch can confirm the wording still exists.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)+$")]
    title: Text
    polarity: Polarity
    paraphrase: Text
    quotes: tuple[Text, ...] = ()
    source_url: HttpsUrl
    snapshot_url: HttpsUrl | None = None
    retrieved: IsoDate
    snapshot_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    version_scope: VersionScope
    applies_to: tuple[Text, ...] = Field(min_length=1)
    notes: str = ""

    @property
    def fetch_url(self) -> str:
        """The document that `snapshot_sha256` hashes: `snapshot_url`, else the source page."""
        return self.snapshot_url or self.source_url.split("#", 1)[0]
