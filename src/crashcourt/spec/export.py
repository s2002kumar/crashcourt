"""JSON Schema export of the v1 models, for the evidence explorer's generated types (F-01).

The committed files in schemas/v1/ must equal what the models render; CI checks it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from crashcourt.manifest import Manifest
from crashcourt.spec.events import Event
from crashcourt.spec.records import BatchRecord, ResultRecord

SCHEMAS: dict[str, type[BaseModel]] = {
    "batch.json": BatchRecord,
    "event.json": Event,
    "manifest.json": Manifest,
    "result.json": ResultRecord,
}


def render(name: str) -> str:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:crashcourt:schema:v1:{name.removesuffix('.json')}",
        **SCHEMAS[name].model_json_schema(),
    }
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in SCHEMAS:
        path = out_dir / name
        path.write_bytes(render(name).encode("utf-8"))
        written.append(path)
    return written


def differences(out_dir: Path) -> list[str]:
    """What differs between the committed schema files and the models; empty means in sync."""
    problems: list[str] = []
    for name in SCHEMAS:
        path = out_dir / name
        if not path.exists():
            problems.append(f"{name} is missing")
        elif path.read_bytes().decode("utf-8") != render(name):
            problems.append(f"{name} differs from the model")
    return problems
