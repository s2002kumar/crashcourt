from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from crashcourt.cli import main
from crashcourt.experiment.gate import FROZEN_PATH, T10Checker
from crashcourt.experiment.preregistration import (
    PREREGISTRATION_PATH,
    PUBLISHED_REPLICATIONS,
    Cell,
    CheckerRecord,
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
    missing = "\n".join(prereg.missing())
    assert "no frozen checker is named" not in missing
    assert "cell K1a: pending" not in missing
    assert "cell A1: pending" in missing  # no fault-free async histories audited yet


def test_the_draft_holds_the_first_matrix_and_its_controls() -> None:
    prereg = parse(committed())
    ids = {c.id for c in prereg.cells}
    assert {"K1a", "K1b", "K2", "K3", "K4", "K5", "N12b", "A4"} <= ids
    assert "E1" not in ids  # deferred before the freeze; the prose records why
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


def _expect(cell: Cell, invariant: str) -> tuple[str, ...] | None:
    return next((p.expect for p in cell.predicted if p.invariant == invariant), None)


def test_the_draft_applies_the_rulings_of_2026_10_10() -> None:
    prereg = parse(committed())
    cells = {c.id: c for c in prereg.cells}
    # P1: a duplicate after a stop at L-EXW, once execute has ended, is GAP on I4 and I7a.
    for name in ("K1a", "A1"):
        assert (_expect(cells[name], "I4"), _expect(cells[name], "I7a")) == (("GAP",), ("GAP",))
    assert _expect(cells["N3"], "I4") == ("GAP",)
    # D1: a second run never suppresses the durability invariants.
    assert _expect(cells["K1a"], "I1b") == ("PASS",)
    assert _expect(cells["N9"], "I1b") == ("PASS",)
    # D2: I4 PASS needs a node that raised; no W1 or W5 cell has one.
    for c in prereg.cells:
        assert "PASS" not in (_expect(c, "I4") or ()), c.id
    for name in ("K1b", "N4", "A2", "A3"):
        assert _expect(cells[name], "I2") == ("NO_ANOMALY_UNSPECIFIED", "GAP")
        assert "LG-PW" not in (cells[name].pending or "")  # the split is decided
    # The replications are scored on their published observations, apart from the outcome.
    assert [o.label() for o in cells["K1a"].observations] == [
        "ledger charge: 2 rows, attempts [1, 2]"
    ]
    assert [o.label() for o in cells["K4"].observations] == [
        "checker observation 'accepted run with no durable record: op start-1 ended': 1"
    ]
    assert cells["K3"].observations
    # P4: no cell is scheduled with SIGSTOP; K3 and N8 hold a worker at a barrier.
    for c in prereg.cells:
        assert "SIGSTOP at" not in c.trigger and "SIGCONT" not in c.trigger, c.id
    for name in ("K3", "N8"):
        assert "held" in cells[name].trigger
    # P5: arms name the audited occurrences.
    assert "occurrence 2" in cells["N1"].trigger  # L-RES
    assert "occurrence 3" in cells["N12"].trigger  # L-T1
    assert "occurrence 3" in cells["K1a"].trigger  # L-EXW
    assert "L-GET" in cells["K2"].trigger


def test_the_draft_pins_the_frozen_checker() -> None:
    """Every hash pinned is the one T-10's FROZEN.md records, and the checker files verify against
    it (freeze.verify_checker raises otherwise)."""
    prereg = parse(committed())
    pin = prereg.checker
    assert pin is not None
    t10 = T10Checker()
    assert t10.freeze_state() == "FROZEN"
    frozen_md = ROOT / FROZEN_PATH
    registry = t10.registry_sha256(ROOT)
    frozen = t10.verify_checker(frozen_md.read_text(encoding="utf-8"), registry)
    assert (pin.version, pin.checker_sha256, pin.registry_sha256) == (
        frozen.version,
        frozen.checker_sha256,
        frozen.registry_sha256,
    )
    assert pin.frozen_md_sha256 == hashlib.sha256(frozen_md.read_bytes()).hexdigest()
    assert (pin.effect_models, pin.landmark_tables) == (
        frozen.effect_models,
        frozen.landmark_tables,
    )
    assert prereg.environment["clause_registry_sha256"] == registry


def test_k4_observes_the_record_as_the_frozen_checker_words_it() -> None:
    """K4 matches checker.json's record by its leading text, so that text is the frozen engine's,
    with K4's op filled in."""
    [observation] = parse(committed()).cell("K4").observations
    assert isinstance(observation, CheckerRecord)
    engine = (ROOT / "src/crashcourt/checker/engine.py").read_text(encoding="utf-8")
    template = 'f"accepted run with no durable record: op {record.spec.op_id} ended "'
    assert template in engine
    assert observation.starts_with == "accepted run with no durable record: op start-1 ended"


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
        ({"observations": []}, "a replication names the published observation"),
        (
            {
                "observations": [
                    {"source": "ledger", "effect": "charge", "rows": 2},
                    {"source": "ledger", "effect": "charge", "rows": 1},
                ]
            },
            "one observation per effect",
        ),
        (
            {
                "observations": [
                    {"source": "ledger", "effect": "charge", "rows": 2, "attempts": [1]}
                ]
            },
            "attempts lists each row's",
        ),
        (
            {
                "observations": [
                    {"source": "ledger", "effect": "charge", "rows": 2, "attempts": [2, 1]}
                ]
            },
            "attempts lists each row's",
        ),
        ({"observations": [{"source": "oracle", "effect": "charge"}]}, "source"),
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
    assert "draft, 36 cells (18 instrument, 18 new evidence)" in out
    assert "before it can be final" in out
