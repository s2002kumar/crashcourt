from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from crashcourt.clauses import ClauseRegistry, Polarity, RegistryError, registry_sha256

HASH = "a" * 64


def clause_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "LG-TEST",
        "title": "A test clause",
        "polarity": "guarantees",
        "paraphrase": "Something the docs say.",
        "quotes": ["Something the docs say, exactly."],
        "source_url": "https://docs.example.com/page#section",
        "retrieved": date(2026, 9, 27),
        "snapshot_sha256": HASH,
        "version_scope": {"package": "langgraph", "specifier": ">=1.2,<1.3"},
        "applies_to": ["AsyncPostgresSaver"],
        "notes": "",
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not _DROP}


_DROP = object()


def write(root: Path, data: dict[str, Any], name: str | None = None, sub: str = "lg") -> Path:
    folder = root / sub
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name or data['id']}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def problems(root: Path) -> str:
    with pytest.raises(RegistryError) as info:
        ClauseRegistry.load(root)
    return str(info.value)


def test_valid_clause_loads_and_is_found_by_id_and_polarity(tmp_path: Path) -> None:
    write(tmp_path, clause_data())
    write(tmp_path, clause_data(id="LG-OTHER", polarity="permits"))
    registry = ClauseRegistry.load(tmp_path)
    assert len(registry) == 2
    clause = registry.get("LG-TEST")
    assert clause.polarity is Polarity.GUARANTEES
    assert clause.retrieved == date(2026, 9, 27)
    assert [c.id for c in registry.by_polarity(Polarity.PERMITS)] == ["LG-OTHER"]
    assert registry.by_polarity(Polarity.DISCLAIMS) == ()
    assert [c.id for c in registry] == ["LG-OTHER", "LG-TEST"]


def test_unknown_id_raises_key_error(tmp_path: Path) -> None:
    write(tmp_path, clause_data())
    with pytest.raises(KeyError, match="LG-NOPE"):
        ClauseRegistry.load(tmp_path).get("LG-NOPE")


@pytest.mark.parametrize("field", ["source_url", "retrieved", "snapshot_sha256", "polarity"])
def test_missing_required_field_is_rejected(tmp_path: Path, field: str) -> None:
    write(tmp_path, clause_data(**{field: _DROP}))
    assert field in problems(tmp_path)


def test_unknown_polarity_is_rejected(tmp_path: Path) -> None:
    write(tmp_path, clause_data(polarity="promises"))
    assert "polarity" in problems(tmp_path)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    write(tmp_path, clause_data(), sub="a")
    write(tmp_path, clause_data(), sub="b")
    assert "duplicate id LG-TEST" in problems(tmp_path)


@pytest.mark.parametrize(
    "value", ["2026-13-01", "27/09/2026", "2026-9-27", 20260927, "2026-09-27T10:00:00", ""]
)
def test_malformed_dates_are_rejected(tmp_path: Path, value: object) -> None:
    write(tmp_path, clause_data(retrieved=value))
    assert "retrieved" in problems(tmp_path)


def test_unquoted_impossible_date_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    path = write(tmp_path, clause_data())
    text = path.read_text(encoding="utf-8").replace(
        "retrieved: 2026-09-27", "retrieved: 2026-13-01"
    )
    path.write_text(text, encoding="utf-8")
    assert "LG-TEST.yaml" in problems(tmp_path)


def test_quoted_iso_date_is_accepted(tmp_path: Path) -> None:
    write(tmp_path, clause_data(retrieved="2026-09-27"))
    assert ClauseRegistry.load(tmp_path).get("LG-TEST").retrieved == date(2026, 9, 27)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot_sha256", "abc"),
        ("snapshot_sha256", "A" * 64),
        ("source_url", "http://docs.example.com/page"),
        ("source_url", "docs.example.com/page"),
        ("snapshot_url", "ftp://example.com/x"),
        ("id", "lg-test"),
        ("applies_to", []),
        ("paraphrase", "   "),
        ("quotes", [""]),
        ("version_scope", {"package": "langgraph", "specifier": "~~1.2"}),
        ("version_scope", {"package": "langgraph", "specifier": ""}),
        ("version_scope", {"package": "", "specifier": ">=1"}),
    ],
)
def test_malformed_values_are_rejected(tmp_path: Path, field: str, value: object) -> None:
    write(tmp_path, clause_data(**{field: value}), name="LG-TEST")
    assert field in problems(tmp_path)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    write(tmp_path, clause_data(sourceurl="https://typo.example.com"))
    assert "sourceurl" in problems(tmp_path)


def test_file_name_must_match_id(tmp_path: Path) -> None:
    write(tmp_path, clause_data(), name="LG-ELSEWHERE")
    assert "does not match id LG-TEST" in problems(tmp_path)


def test_non_mapping_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "LG-LIST.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    assert "mapping" in problems(tmp_path)


def test_every_problem_is_reported_at_once(tmp_path: Path) -> None:
    write(tmp_path, clause_data(id="LG-ONE", polarity="nope"))
    write(tmp_path, clause_data(id="LG-TWO", snapshot_sha256="short"))
    text = problems(tmp_path)
    assert "LG-ONE.yaml" in text and "LG-TWO.yaml" in text


def test_empty_directory_is_rejected(tmp_path: Path) -> None:
    assert "no clause files" in problems(tmp_path)


def test_version_scope_membership(tmp_path: Path) -> None:
    write(tmp_path, clause_data())
    scope = ClauseRegistry.load(tmp_path).get("LG-TEST").version_scope
    assert scope.contains("1.2.12")
    assert not scope.contains("1.3.0")


def test_fetch_url_defaults_to_source_without_fragment(tmp_path: Path) -> None:
    write(tmp_path, clause_data())
    write(tmp_path, clause_data(id="LG-MD", snapshot_url="https://docs.example.com/page.md"))
    registry = ClauseRegistry.load(tmp_path)
    assert registry.get("LG-TEST").fetch_url == "https://docs.example.com/page"
    assert registry.get("LG-MD").fetch_url == "https://docs.example.com/page.md"


def test_yml_files_are_reported_not_skipped(tmp_path: Path) -> None:
    write(tmp_path, clause_data())
    write(tmp_path, clause_data(id="LG-OTHER"))
    (tmp_path / "lg" / "LG-OTHER.yaml").rename(tmp_path / "lg" / "LG-OTHER.yml")
    assert "lg/LG-OTHER.yml: clause files must end in .yaml" in problems(tmp_path)


def test_registry_digest_ignores_line_endings_and_tracks_every_change(tmp_path: Path) -> None:
    path = write(tmp_path, clause_data())
    lf = path.read_bytes().replace(b"\r\n", b"\n")  # write() uses the platform's line endings
    path.write_bytes(lf)
    first = registry_sha256(tmp_path)
    path.write_bytes(lf.replace(b"\n", b"\r\n"))
    assert registry_sha256(tmp_path) == first
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    edited = registry_sha256(tmp_path)
    assert edited != first
    path.rename(path.with_name("LG-RENAMED.yaml"))
    assert registry_sha256(tmp_path) not in {first, edited}
