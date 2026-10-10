"""Version and hash plumbing for the checker freeze.

The freeze is enforced by content hash in git order: `FROZEN.md` (written only by the freeze
commit) lists, for the checker version, the sha256 of every file under checker/, of the clause
registry, of each workload's effect model, and of each scenario's landmark table. The commit that
writes it must be an ancestor of the first new-evidence batch, and the pre-registration names its
checker hash. After the freeze, any change to a predicate, a clause mapping, an effect model, or a
landmark table produces v2; nothing is relabeled in place.

PRE-FREEZE: this module computes and compares the hashes; it does not write FROZEN.md, and a test
fails if the file exists while the checker is PRE-FREEZE.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from crashcourt.checker.artifacts import LandmarkTable
from crashcourt.checker.effect_models import EFFECT_MODELS
from crashcourt.checker.status import CHECKER_VERSION
from crashcourt.spec.content import content_sha256

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

CHECKER_ROOT = Path(__file__).resolve().parent
FROZEN_PATH = CHECKER_ROOT / "FROZEN.md"
_FENCE = "```json"


class FreezeMismatch(Exception):
    """The checker on disk is not the one FROZEN.md records."""


class FreezeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checker_version: str
    #: sha256 of each checker source file, by its path relative to checker/.
    files: dict[str, Sha256]
    #: sha256 over every checker file's path and bytes: the checker's identity.
    checker_sha256: Sha256
    registry_sha256: Sha256
    effect_models: dict[str, Sha256]
    landmark_tables: dict[str, Sha256]


def checker_files(root: Path = CHECKER_ROOT) -> list[Path]:
    """Every Python source file of the checker, in POSIX path order on every platform."""
    return sorted(root.rglob("*.py"), key=lambda p: p.relative_to(root).as_posix())


def _bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def checker_sha256(root: Path = CHECKER_ROOT) -> str:
    """sha256 over each checker file's relative path and LF-normalized bytes."""
    digest = hashlib.sha256()
    for path in checker_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(_bytes(path) + b"\0")
    return digest.hexdigest()


def effect_model_hashes() -> dict[str, str]:
    return {name: content_sha256(model) for name, model in sorted(EFFECT_MODELS.items())}


def landmark_hash(table: LandmarkTable) -> str:
    return content_sha256(table)


def compute(
    registry_sha256: str, landmark_tables: Iterable[LandmarkTable], root: Path = CHECKER_ROOT
) -> FreezeManifest:
    return FreezeManifest(
        checker_version=CHECKER_VERSION,
        files={
            p.relative_to(root).as_posix(): hashlib.sha256(_bytes(p)).hexdigest()
            for p in checker_files(root)
        },
        checker_sha256=checker_sha256(root),
        registry_sha256=registry_sha256,
        effect_models=effect_model_hashes(),
        landmark_tables={
            t.name: landmark_hash(t) for t in sorted(landmark_tables, key=lambda t: t.name)
        },
    )


def render(manifest: FreezeManifest) -> str:
    """The FROZEN.md text the freeze commit writes: a heading and the manifest as JSON."""
    body = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
    return (
        f"# Checker {manifest.checker_version}: frozen\n\n"
        f"Checker hash `{manifest.checker_sha256}`.\n\n{_FENCE}\n{body}\n```\n"
    )


def parse(text: str) -> FreezeManifest:
    start = text.index(_FENCE) + len(_FENCE)
    end = text.index("```", start)
    return FreezeManifest.model_validate_json(text[start:end])


def differences(frozen: FreezeManifest, current: FreezeManifest) -> list[str]:
    """Every way the current checker differs from the frozen one."""
    found: list[str] = []
    for name in ("checker_version", "checker_sha256", "registry_sha256"):
        if getattr(frozen, name) != getattr(current, name):
            found.append(f"{name}: frozen {getattr(frozen, name)}, now {getattr(current, name)}")
    for field in ("files", "effect_models", "landmark_tables"):
        then: dict[str, str] = getattr(frozen, field)
        now: dict[str, str] = getattr(current, field)
        for key in sorted(then.keys() | now.keys()):
            if then.get(key) != now.get(key):
                found.append(f"{field}[{key}]: frozen {then.get(key)}, now {now.get(key)}")
    return found


def verify_trial(
    text: str, registry_sha256: str, table: LandmarkTable | None, root: Path = CHECKER_ROOT
) -> FreezeManifest:
    """The frozen manifest, if the checker files, effect models, and registry match it and the
    trial's landmark table (if it has one) is one of the frozen tables; FreezeMismatch otherwise.
    A checker that does not match its freeze refuses to classify (section 0.3: a batch whose
    checker hash is not the pre-registered one fails)."""
    frozen = parse(text)
    now = compute(registry_sha256, [], root)
    found = [d for d in differences(frozen, now) if not d.startswith("landmark_tables[")]
    if table is not None and frozen.landmark_tables.get(table.name) != landmark_hash(table):
        found.append(
            f"landmark_tables[{table.name}]: frozen {frozen.landmark_tables.get(table.name)}, "
            f"now {landmark_hash(table)}"
        )
    if found:
        raise FreezeMismatch("; ".join(found))
    return frozen


def verify(text: str, current: FreezeManifest) -> FreezeManifest:
    """The frozen manifest, if the current checker matches it; FreezeMismatch otherwise."""
    frozen = parse(text)
    found = differences(frozen, current)
    if found:
        raise FreezeMismatch("; ".join(found))
    return frozen
