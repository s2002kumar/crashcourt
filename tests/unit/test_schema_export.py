from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashcourt.cli import main
from crashcourt.spec.export import SCHEMAS, differences, render

ROOT = Path(__file__).resolve().parents[2]


def test_committed_schemas_match_the_models() -> None:
    committed = ROOT / "schemas" / "v1"
    assert differences(committed) == [], "regenerate with: uv run crashcourt schema export"


def test_rendering_is_stable_and_self_describing() -> None:
    for name in SCHEMAS:
        text = render(name)
        assert text == render(name)
        assert text.endswith("}\n")
        schema = json.loads(text)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"urn:crashcourt:schema:v1:{name.removesuffix('.json')}"


def test_event_schema_lists_every_kind() -> None:
    from crashcourt.spec.events import EVENT_KINDS

    schema = json.loads(render("event.json"))
    assert sorted(schema["properties"]["kind"]["enum"]) == sorted(EVENT_KINDS)


def test_export_then_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "v1"
    assert main(["schema", "export", "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(SCHEMAS)
    assert all(b"\r" not in p.read_bytes() for p in out.iterdir())
    assert main(["schema", "export", "--out", str(out), "--check"]) == 0
    (out / "event.json").write_text("{}\n", encoding="utf-8")
    (out / "manifest.json").unlink()
    assert main(["schema", "export", "--out", str(out), "--check"]) == 1
    text = capsys.readouterr().out
    assert "event.json differs" in text and "manifest.json is missing" in text
