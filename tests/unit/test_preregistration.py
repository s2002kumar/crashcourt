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
    BUILDER_PATH,
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


def test_the_draft_can_authorize_k1a_and_never_new_evidence() -> None:
    prereg = parse(committed())
    assert prereg.runnable() == ["K1a"]
    for c in prereg.cells:
        if c.phase == "new-evidence":
            assert prereg.refusals(c) == [
                f"the pre-registration is a draft; new-evidence cell {c.id} needs it final"
            ]


def test_every_written_scenario_is_its_cells() -> None:
    """A cell whose scenario file exists names that file's hash, master seed, and workers, and
    the file's landmark table is one the freeze pins; a cell with a hash has its file."""
    from crashcourt.checker import freeze
    from crashcourt.clauses import registry_sha256
    from crashcourt.experiment.gate import FROZEN_PATH
    from crashcourt.spec.scenario import load_scenario, scenario_sha256

    prereg = parse(committed())
    assert prereg.checker is not None
    frozen_md = (ROOT / FROZEN_PATH).read_text(encoding="utf-8")
    registry = registry_sha256(ROOT / "clauses")
    written = [c for c in prereg.cells if (ROOT / c.scenario).is_file()]
    assert [c.id for c in written] == ["K1a"]
    for c in prereg.cells:
        assert (c.scenario_sha256 is None) == (c not in written), c.id
    for c in written:
        scenario = load_scenario(ROOT / c.scenario)
        assert (scenario_sha256(scenario), scenario.seed, scenario.cell) == (
            c.scenario_sha256,
            c.master_seed,
            c.id,
        )
        assert len(scenario.workers) == c.workers
        table = freeze.verify_scenario(frozen_md, registry, ROOT / c.scenario)
        assert table in prereg.checker.landmark_tables.values()


def test_k1a_runs_the_shape_t11_validated() -> None:
    """K1a's scenario is T-11's K1a shape (research/evidence/t-11/shapes.json) under K1a's own
    name, cell, and master seed: nothing else differs."""
    from crashcourt.spec.scenario import load_scenario

    other = {"name", "cell", "seed"}
    k1a = load_scenario(ROOT / "scenarios/first-matrix/K1a.yaml")
    shape = load_scenario(ROOT / "tests/integration/scenarios/t11-k1a-shape.yaml")
    assert k1a.model_dump(exclude=other) == shape.model_dump(exclude=other)


def test_a_redelivery_cell_declares_its_replacement() -> None:
    """A redelivered op goes to another worker the scenario declares, so a cell whose trigger
    redelivers has two workers; the gate holds each scenario to its cell's count."""
    cells = {c.id: c for c in parse(committed()).cells}
    redelivering = {i for i, c in cells.items() if "redeliver" in c.trigger}
    assert redelivering == {"K1a", "K1b", "K3", "N8", "N11", "A1", "A2"}
    assert all(cells[i].workers == 2 for i in redelivering)


def test_the_draft_pins_the_environment_it_runs_in() -> None:
    """The canonical runner (decision 17 in tasks/T-13.md), and the lockfile and specification
    hashes doctor records: a uv.lock change moves its pin in the same commit."""
    env = parse(committed()).environment
    assert env["runner"] == "github-hosted"
    assert env["lockfile_sha256"] == hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    provenance = json.loads((ROOT / "spec-provenance.json").read_text(encoding="utf-8"))
    assert env["spec_sha256"] == provenance["sha256"]
    assert all(value is not None for value in env.values())


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


def test_every_instrument_cell_predicts_every_invariant_the_aggregator_can_score() -> None:
    """The aggregator reads an exercised invariant that a cell does not name as `recorded`, and the
    instrument gate refuses a cell that is not as predicted (K1a's first formal batch,
    K1a-gh38926456031-1). So every instrument cell has a prediction for every invariant checker v1
    reports, as the aggregator itself looks it up."""
    from crashcourt.checker.invariants import INVARIANTS
    from crashcourt.experiment.aggregate import summarize_invariant
    from crashcourt.experiment.verify import TrialFiles

    instrument = [c for c in parse(committed()).cells if c.phase == "instrument"]
    assert {"K1a", "K2", "K3", "K4", "K5", "K5-reject"} <= {c.id for c in instrument}
    for cell in instrument:
        for invariant in INVARIANTS:
            outcome = {"invariant": invariant, "outcome": "NO_ANOMALY_UNSPECIFIED"}
            trial = TrialFiles(1, ROOT, {"not_exercised": []}, {"results": [outcome]})
            summary = summarize_invariant(cell, invariant, [trial])
            assert summary.predicted is not None, (cell.id, invariant)
            assert summary.prediction != "recorded", (cell.id, invariant)


def test_the_completeness_ruling_of_2026_10_16() -> None:
    text = committed()
    cells = {c.id: c for c in parse(text).cells}
    no_other = ("PASS", "NO_ANOMALY_UNSPECIFIED", "NOT_EXERCISED")
    for name in ("K1a", "K2", "K3", "K5", "K5-reject"):
        assert _expect(cells[name], "*") == no_other, name
    assert _expect(cells["K4"], "*") == ("NOT_EXERCISED",)  # P2: K4 scores no outcome
    # K1a's named predictions are the ones its first batch ran under; only the default is new.
    k1a = cells["K1a"]
    assert [_expect(k1a, i) for i in ("I4", "I7a", "I1b")] == [("GAP",), ("GAP",), ("PASS",)]
    deviations = text[text.index("## Deviations") : text.index("## Pre-registration data")]
    assert "K1a-gh38926456031-1" in deviations
    assert "never counted as a passing replication" in deviations
    # I7c, corrected before either cell ran: checker v1 exercises it on w1-misuse.
    for name in ("K5", "K5-reject"):
        assert _expect(cells[name], "I7c") == ("NO_ANOMALY_UNSPECIFIED",)
        assert cells[name].pending is None


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


#: Checker v1 as T-10 froze it in 2802584 (#28). Nothing in T-13 may change these.
CHECKER_V1 = {
    "checker_sha256": "d80951681c2b8656a08647d3ba60ee3b9b025e1ff118d83796ccdb433f111097",
    "frozen_md_sha256": "ddff87d4d487ef211d385279951eaa2be37c71394956fcc0c4ff541667338e12",
    "registry_sha256": "0c25b2d96620a0751580e3d904cba8d81040d8d2b4cb007826ae2ceab46eb5c5",
    "effect_models": {
        "w1": "7e0376323ac016537edf5b1abb4641efba36519d40df58d9762817158e857859",
        "w1-misuse": "d750e43ba865dc834a7341def0e19727bec72e5fd96da2ddf4ec7d2f467b1b5f",
        "w5": "74d96b6a84043e5cbd7ef8e3386bba50c191572dff16284bb1664e991dc6f8af",
    },
    "landmark_tables": {
        "langgraph-v1": "279e5e441b14dafca556d8b8824afbcc4698ce0d073a63f324386956160682f4",
    },
}


def test_the_frozen_checker_is_still_v1() -> None:
    """The checker, FROZEN.md, the clause registry, the effect models, and the audited landmark
    table are byte for byte the ones frozen in 2802584, and the draft still pins exactly them."""
    t10 = T10Checker()
    frozen_md = ROOT / FROZEN_PATH
    frozen = t10.verify_checker(frozen_md.read_text(encoding="utf-8"), t10.registry_sha256(ROOT))
    assert {
        "checker_sha256": frozen.checker_sha256,
        "frozen_md_sha256": hashlib.sha256(frozen_md.read_bytes()).hexdigest(),
        "registry_sha256": frozen.registry_sha256,
        "effect_models": frozen.effect_models,
        "landmark_tables": frozen.landmark_tables,
    } == CHECKER_V1
    pin = parse(committed()).checker
    assert pin is not None
    assert pin.model_dump(exclude={"version"}) == CHECKER_V1


def test_the_draft_pins_the_builder_as_it_is() -> None:
    """orchestrator/declared.py is outside the freeze; the draft pins its bytes, so changing it
    without updating the pin fails here first."""
    builder = parse(committed()).builder
    assert builder is not None and builder.path == BUILDER_PATH
    assert builder.sha256 == hashlib.sha256((ROOT / BUILDER_PATH).read_bytes()).hexdigest()


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
    with pytest.raises(ValidationError, match="no TrialScenario builder is pinned"):
        prereg_model(builder=None)
    with pytest.raises(ValidationError, match=r"builder\.path"):
        prereg_model(builder={"path": "src/crashcourt/other.py", "sha256": "0" * 64})
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
    assert "the draft can authorize: K1a\n" in out
    assert "before it can be final" in out
