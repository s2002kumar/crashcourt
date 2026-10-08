from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from crashcourt.cli import main
from crashcourt.spec.export import SCHEMAS, differences, render

ROOT = Path(__file__).resolve().parents[2]

#: v1 is frozen. A change to these files, docstrings included, needs schema v2, not new pins.
FROZEN_V1 = {
    "batch.json": "a30daba0f915e8b311404581cadb770a1dc80d244dc3f275e6e93c17c6a2b473",
    "event.json": "eb5d89e4185e20052e520c96c89988cc83b0ad3b5a734df4cb0357c468cf31e6",
    "manifest.json": "05951ea3e12b04767b5c1a69d6294aa770039aa45c7c03ae9cfa17deec0e6fa1",
    "result.json": "094a158f65890202d8a5649db9c800a1a65c1cd293b4c67749eccf312c3e3c86",
}


def test_committed_schemas_match_the_models() -> None:
    committed = ROOT / "schemas" / "v1"
    assert differences(committed) == [], "regenerate with: uv run crashcourt schema export"


def test_v1_schemas_are_frozen() -> None:
    assert sorted(FROZEN_V1) == sorted(SCHEMAS)
    for name, digest in FROZEN_V1.items():
        committed = (ROOT / "schemas" / "v1" / name).read_bytes()
        assert hashlib.sha256(committed).hexdigest() == digest, f"schemas/v1/{name} changed"


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
