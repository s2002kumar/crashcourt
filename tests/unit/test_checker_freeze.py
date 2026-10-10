"""The freeze state, the hash plumbing a freeze will use, and the checker's static guarantees."""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
import yaml

from crashcourt.checker import freeze, status
from crashcourt.checker.artifacts import ContextRule, LandmarkSpec, LandmarkTable
from crashcourt.checker.effect_models import EFFECT_MODELS
from crashcourt.checker.landmarks import LANDMARKS_ROOT, repository_tables
from crashcourt.checker.status import EvidenceClass, NotAuthorized, authorize
from tests.unit import _trials as T

_, REGISTRY_SHA = T.registry()
TABLE = LandmarkTable(
    name="DEMO-landmarks",
    status="draft",
    landmarks=(LandmarkSpec(id="L-X", method="put", rules=(ContextRule(rule="last_call"),)),),
)


# -- PRE-FREEZE ---------------------------------------------------------------------------------


def test_the_frozen_record_matches_the_checker() -> None:
    """Mandatory in both states. Before the freeze (PRE-FREEZE / NOT AUTHORIZED FOR NEW-EVIDENCE
    CLASSIFICATION) there is no FROZEN.md; only the freeze commit, after the landmark audit,
    writes it and flips the state. After the freeze, FROZEN.md must match the checker files, the
    effect models, the clause registry, and every landmark table in the repository, byte for
    byte; any change to any of them fails here and needs a new checker version."""
    if status.FREEZE_STATE == "PRE-FREEZE":
        assert not freeze.FROZEN_PATH.exists()
        return
    assert status.FREEZE_STATE == "FROZEN"
    text = freeze.FROZEN_PATH.read_text(encoding="utf-8")
    frozen = freeze.verify_checker(text, REGISTRY_SHA)
    tables = repository_tables()
    assert all(t.status == "audited" for t in tables)
    assert set(frozen.landmark_tables) == {t.name for t in tables}
    for table in tables:
        freeze.verify_trial(text, REGISTRY_SHA, table)


def test_the_checker_is_frozen_with_an_audited_table() -> None:
    assert status.FREEZE_STATE == "FROZEN"
    assert freeze.FROZEN_PATH.exists()
    assert [t.status for t in repository_tables()] == ["audited"]


def test_the_frozen_checker_refuses_no_evidence_class() -> None:
    """Frozen, any trial may be classified: the gate is FROZEN.md, checked per trial."""
    classes: tuple[EvidenceClass, ...] = (
        "synthetic",
        "artifact-mutant",
        "control",
        "replication",
        "new-evidence",
    )
    for evidence in classes:
        authorize(evidence, None, "N4")


@pytest.mark.usefixtures("pre_freeze")
@pytest.mark.parametrize("evidence", ["synthetic", "artifact-mutant", "control"])
def test_pre_freeze_inputs_are_allowed(evidence: EvidenceClass) -> None:
    authorize(evidence, None, None)


@pytest.mark.usefixtures("pre_freeze")
@pytest.mark.parametrize("replicated", sorted(status.PUBLISHED_REPLICATIONS))
def test_the_three_published_replications_are_allowed(replicated: str) -> None:
    authorize("replication", replicated, None)


@pytest.mark.parametrize(
    ("evidence", "replicated", "cell"),
    [
        ("new-evidence", None, None),
        ("replication", "langgraph#9185", None),
        ("replication", None, None),
        ("control", "langgraph#8039", None),
        ("control", None, "N1"),
        ("synthetic", None, "N12b"),
        ("replication", "langgraph#8039", "K1b"),
        ("control", None, "A3"),
        ("control", None, "E1"),
    ],
)
@pytest.mark.usefixtures("pre_freeze")
def test_everything_else_is_refused(
    evidence: EvidenceClass, replicated: str | None, cell: str | None
) -> None:
    with pytest.raises(NotAuthorized):
        authorize(evidence, replicated, cell)


# -- hashes -------------------------------------------------------------------------------------


def test_the_manifest_covers_every_checker_file_and_model() -> None:
    manifest = freeze.compute(REGISTRY_SHA, [TABLE])
    on_disk = {
        p.relative_to(freeze.CHECKER_ROOT).as_posix() for p in freeze.CHECKER_ROOT.rglob("*.py")
    }
    assert set(manifest.files) == on_disk
    assert set(manifest.effect_models) == set(EFFECT_MODELS)
    assert manifest.landmark_tables == {"DEMO-landmarks": freeze.landmark_hash(TABLE)}
    assert manifest.registry_sha256 == REGISTRY_SHA
    assert manifest.checker_version == status.CHECKER_VERSION


def test_a_rendered_record_round_trips_and_verifies() -> None:
    manifest = freeze.compute(REGISTRY_SHA, [TABLE])
    text = freeze.render(manifest)
    assert freeze.parse(text) == manifest
    assert freeze.verify(text, manifest) == manifest


def test_any_change_is_a_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "checker"
    shutil.copytree(freeze.CHECKER_ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    frozen = freeze.render(freeze.compute(REGISTRY_SHA, [TABLE], root))
    (root / "mapping.py").write_text((root / "mapping.py").read_text() + "\n# edit\n")
    with pytest.raises(freeze.FreezeMismatch, match=r"mapping.py"):
        freeze.verify(frozen, freeze.compute(REGISTRY_SHA, [TABLE], root))
    other_table = TABLE.model_copy(update={"landmarks": ()})
    with pytest.raises(freeze.FreezeMismatch, match="landmark_tables"):
        freeze.verify(frozen, freeze.compute(REGISTRY_SHA, [other_table], root))
    with pytest.raises(freeze.FreezeMismatch, match="registry_sha256"):
        freeze.verify(frozen, freeze.compute("0" * 64, [TABLE], root))


def test_the_checker_hash_ignores_line_endings(tmp_path: Path) -> None:
    root = tmp_path / "checker"
    shutil.copytree(freeze.CHECKER_ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    before = freeze.checker_sha256(root)
    path = root / "status.py"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert freeze.checker_sha256(root) == before


def test_each_effect_model_has_its_own_hash() -> None:
    hashes = freeze.effect_model_hashes()
    assert len(set(hashes.values())) == len(hashes)


# -- static guarantees --------------------------------------------------------------------------

ALLOWED_LANGGRAPH = {"langgraph.checkpoint.serde.jsonplus"}
FORBIDDEN_MODULES = {
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "psycopg",
    "psycopg_pool",
    "sqlite3",
    "subprocess",
    "asyncio",
    "time",
    "random",
    "crashcourt.failpoints",
    "crashcourt.sink",
    "crashcourt.faults",
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_checker_imports_nothing_that_touches_the_network_or_the_target() -> None:
    for path in freeze.checker_files():
        for name in _imports(path):
            top = name.split(".")[0]
            assert top not in FORBIDDEN_MODULES and name not in FORBIDDEN_MODULES, (path, name)
            assert not any(name.startswith(f"{m}.") for m in FORBIDDEN_MODULES), (path, name)
            if top == "langgraph":
                assert name in ALLOWED_LANGGRAPH, (path, name)


def test_the_checker_never_reads_a_monotonic_clock() -> None:
    """mono_ns is for display only: no checker code reads it (as an attribute or a key), and the
    checker does not call the merger, which breaks ties with it. The ledger row model declares
    T-08's received_mono_ns column; declaring it is not reading it."""
    for path in freeze.checker_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert "mono_ns" not in node.attr, (path, node.lineno)
            keys: list[ast.expr] = []
            if isinstance(node, ast.Subscript):
                keys = [node.slice]
            elif isinstance(node, ast.Call):
                keys = list(node.args)
            for key in keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    assert "mono_ns" not in key.value, (path, key.lineno)
            if isinstance(node, ast.Name):
                assert node.id != "merge", (path, node.lineno)
            if isinstance(node, ast.ImportFrom) and node.module == "crashcourt.history.merge":
                assert all(a.name != "merge" for a in node.names), path


def test_the_checker_never_writes_violation() -> None:
    """VIOLATION is written by hand after the escalation chain; no checker file sets it."""
    for path in freeze.checker_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value == "VIOLATION":
                pytest.fail(f"{path} names the VIOLATION outcome")


def test_no_landmark_occurrence_is_encoded_in_the_checker() -> None:
    """Landmarks are data (the scenario's audited table); the checker holds no landmark ids."""
    for path in freeze.checker_files():
        text = path.read_text(encoding="utf-8")
        for landmark in ("L-IN", "L-PLAN", "L-INT", "L-RES", "L-EXW", "L-EXC", "L-FIN", "L-T1"):
            assert landmark not in text, (path, landmark)


def test_a_scenario_pins_its_landmark_table(tmp_path: Path) -> None:
    """The batch gate's per-cell check: the scenario's `landmarks` key names an audited table that
    is one of the frozen tables; the result is the hash checker.json carries."""
    landmarks = tmp_path / "landmarks"
    landmarks.mkdir()
    audited = LandmarkTable(
        name="demo",
        status="audited",
        landmarks=(TABLE.landmarks[0].model_copy(update={"occurrence": 1}),),
    )
    (landmarks / "demo.yaml").write_text(
        yaml.safe_dump(audited.model_dump(mode="json")), encoding="utf-8"
    )
    text = freeze.render(freeze.compute(REGISTRY_SHA, [audited]))
    assert freeze.verify_landmarks(text, REGISTRY_SHA, "demo", landmarks) == freeze.landmark_hash(
        audited
    )
    assert freeze.verify_landmarks(text, REGISTRY_SHA, None, landmarks) is None
    with pytest.raises(freeze.FreezeMismatch):
        freeze.verify_landmarks(text, REGISTRY_SHA, "missing", landmarks)
    for outside in ("../demo", "demo/x", "demo yaml", ""):
        with pytest.raises(freeze.FreezeMismatch):
            freeze.verify_landmarks(text, REGISTRY_SHA, outside, landmarks)
    draft = TABLE.model_copy(update={"name": "demo"})
    (landmarks / "demo.yaml").write_text(
        yaml.safe_dump(draft.model_dump(mode="json")), encoding="utf-8"
    )
    with pytest.raises(freeze.FreezeMismatch):
        freeze.verify_landmarks(text, REGISTRY_SHA, "demo", landmarks)
    scenario = tmp_path / "cell.yaml"
    scenario.write_text("v: 1\nlandmarks: [not, a, name]\n", encoding="utf-8")
    with pytest.raises(freeze.FreezeMismatch):
        freeze.verify_scenario(text, REGISTRY_SHA, scenario)
    scenario.write_text("v: 1\nname: cell\n", encoding="utf-8")
    assert freeze.verify_scenario(text, REGISTRY_SHA, scenario) is None


def test_repository_tables_are_named_by_their_files() -> None:
    assert [t.name for t in repository_tables()] == [
        p.stem for p in sorted(LANDMARKS_ROOT.glob("*.yaml"), key=lambda p: p.name)
    ]
