from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from crashcourt.cli import main
from crashcourt.experiment.preregistration import (
    PREREGISTRATION_PATH,
    PUBLISHED_REPLICATIONS,
    PreregistrationError,
    block,
    parse,
)

from ._experiment import prereg_model, prereg_text

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / PREREGISTRATION_PATH


def committed() -> str:
    return COMMITTED.read_text(encoding="utf-8")


def test_the_committed_preregistration_is_a_valid_draft() -> None:
    prereg = parse(committed())
    assert prereg.state == "draft"
    assert prereg.checker is None
    missing = "\n".join(prereg.missing())
    assert "no frozen checker is named" in missing
    assert "cell K1a: pending" in missing


def test_the_draft_holds_the_first_matrix_and_its_controls() -> None:
    prereg = parse(committed())
    ids = {c.id for c in prereg.cells}
    assert {"K1a", "K1b", "K2", "K3", "K4", "K5", "N12b", "A4", "E1"} <= ids
    assert {f"N{n}" for n in range(1, 13)} <= ids
    instrument = {c.id for c in prereg.cells if c.phase == "instrument"}
    replications = {c.replication_of for c in prereg.cells if c.evidence == "replication"}
    assert replications == PUBLISHED_REPLICATIONS
    assert {"K1a", "K3", "K4", "K2", "K5"} <= instrument
    first = [c for c in prereg.cells if c.phase == "instrument"]
    assert all(c.id.startswith(("C1-", "C3-", "K")) for c in first)
    assert all(c.n == 20 for c in prereg.cells if c.id[0] in "KNAE")
    assert all(c.n == 50 for c in prereg.cells if c.control in ("C1", "C3"))
    assert all(c.deterministic for c in prereg.cells)


def test_the_cell_table_matches_the_data() -> None:
    text = committed()
    for cell in parse(text).cells:
        assert f"| {cell.id} | {cell.phase} |" in text


def test_the_block_round_trips() -> None:
    prereg = parse(committed())
    assert parse(block(prereg)) == prereg


def test_a_final_preregistration_must_be_complete() -> None:
    assert prereg_model().state == "final"
    with pytest.raises(ValidationError, match="no frozen checker"):
        prereg_model(checker=None)
    cells: list[dict[str, Any]] = json.loads(prereg_model().model_dump_json())["cells"]
    cells[0]["scenario_sha256"] = None
    with pytest.raises(ValidationError, match="cell R1: no scenario hash"):
        prereg_model(cells=cells)
    with pytest.raises(ValidationError, match="environment runner is not pinned"):
        prereg_model(environment={"runner": None})
    with pytest.raises(ValidationError, match="instrument check AM"):
        prereg_model(instrument_checks=[{"id": "AM", "requirement": "mutants"}])


def _cells_with(**change: Any) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = json.loads(prereg_model().model_dump_json())["cells"]
    cells[0].update(change)
    return cells


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"replication_of": "langgraph#9185"}, "replication_of must be one of"),
        ({"phase": "new-evidence"}, "new-evidence cells, and only they"),
        ({"predicted": [{"invariant": "I4", "expect": ["VIOLATION"]}]}, "cannot predict"),
        ({"predicted": [{"invariant": "I4", "expect": ["PASS"]}] * 2}, "one prediction per"),
        ({"n": 0}, "greater than or equal to 1"),
    ],
)
def test_cells_are_checked(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        prereg_model(cells=_cells_with(**change))


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("id", "cell ids repeat"),
        ("scenario", "scenario files repeat"),
        ("scenario_sha256", "scenario hashes repeat"),
    ],
)
def test_a_scenario_names_exactly_one_cell(field: str, message: str) -> None:
    cells = _cells_with()
    cells[1][field] = cells[0][field]
    with pytest.raises(ValidationError, match=message):
        prereg_model(cells=cells)


def test_parse_needs_exactly_one_block() -> None:
    text = prereg_text(prereg_model())
    with pytest.raises(PreregistrationError, match="exactly one"):
        parse("no block here")
    with pytest.raises(PreregistrationError, match="exactly one"):
        parse(text + text)


def test_cli_check_reports_what_is_missing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["experiment", "check", "--root", str(ROOT)]) == 0
    out = capsys.readouterr().out
    assert "draft, 37 cells (18 instrument, 19 new evidence)" in out
    assert "before it can be final" in out
