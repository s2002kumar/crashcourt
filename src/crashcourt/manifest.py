"""The environment manifest every result is stamped with (spec sections 9 and 13, control C7)."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

#: Distributions under test or on the persistence path; their versions are part of every result.
PINNED_DISTRIBUTIONS: tuple[str, ...] = (
    "langgraph",
    "langgraph-checkpoint",
    "langgraph-checkpoint-postgres",
    "langgraph-checkpoint-sqlite",
    "psycopg",
    "psycopg-pool",
)

#: Postgres settings recorded in every manifest. track_commit_timestamp must be "on" (spec section 8).
POSTGRES_SETTINGS: tuple[str, ...] = (
    "server_version",
    "fsync",
    "synchronous_commit",
    "track_commit_timestamp",
    "default_transaction_isolation",
    "max_connections",
)

#: Compose services whose images are part of the record.
REQUIRED_SERVICES: tuple[str, ...] = ("postgres", "toxiproxy")

DIGEST_MARKER = "@sha256:"

_TEXT_FIELDS: tuple[str, ...] = (
    "generated_at",
    "python_version",
    "python_implementation",
    "os_system",
    "machine",
    "docker_version",
    "compose_version",
    "toxiproxy_version",
)


@dataclass(frozen=True)
class Manifest:
    generated_at: str
    git_sha: str
    git_dirty: bool
    python_version: str
    python_implementation: str
    os_system: str
    os_release: str
    machine: str
    lockfile_sha256: str
    packages: dict[str, str]
    docker_version: str
    compose_version: str
    images: dict[str, str]
    postgres: dict[str, str]
    toxiproxy_version: str
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        data: dict[str, Any] = json.loads(text)
        return cls(**data)


def validate_manifest(m: Manifest) -> list[str]:
    """Return every problem that makes a manifest unusable as evidence; empty means complete."""
    problems: list[str] = []
    if not m.git_sha:
        problems.append("git_sha is missing")
    if not m.lockfile_sha256:
        problems.append("lockfile_sha256 is missing")
    for name in _TEXT_FIELDS:
        if not getattr(m, name):
            problems.append(f"{name} is missing")
    for dist in PINNED_DISTRIBUTIONS:
        if not m.packages.get(dist):
            problems.append(f"package version missing: {dist}")
    for service in REQUIRED_SERVICES:
        ref = m.images.get(service, "")
        if DIGEST_MARKER not in ref:
            problems.append(f"image not pinned by digest: {service} ({ref or 'missing'})")
    for setting in POSTGRES_SETTINGS:
        if not m.postgres.get(setting):
            problems.append(f"postgres setting missing: {setting}")
    tct = m.postgres.get("track_commit_timestamp")
    if tct and tct != "on":
        problems.append(f"track_commit_timestamp must be on, got {tct}")
    return problems


def pinned_view(m: Manifest) -> dict[str, Any]:
    """Fields that must match for two runs to be aggregated (C7). Host details are recorded only."""
    return {
        "schema_version": m.schema_version,
        "python_minor": ".".join(m.python_version.split(".")[:2]),
        "lockfile_sha256": m.lockfile_sha256,
        "packages": dict(sorted(m.packages.items())),
        "images": dict(sorted(m.images.items())),
        "postgres": dict(sorted(m.postgres.items())),
        "toxiproxy_version": m.toxiproxy_version,
    }


def diff_pinned(a: Manifest, b: Manifest) -> list[str]:
    """Human-readable differences between the pinned views of two manifests."""
    va, vb = pinned_view(a), pinned_view(b)
    return [f"{key}: {va[key]!r} != {vb[key]!r}" for key in va if va[key] != vb[key]]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_dist(name: str) -> str:
    """PEP 503 normalization, as used in uv.lock."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_uv_lock(text: str) -> dict[str, str]:
    """Map normalized distribution name to its locked version."""
    data = tomllib.loads(text)
    packages: list[Any] = data.get("package", [])
    locked: dict[str, str] = {}
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        name: Any = entry.get("name")  # pyright: ignore[reportUnknownMemberType]
        ver: Any = entry.get("version")  # pyright: ignore[reportUnknownMemberType]
        if isinstance(name, str) and isinstance(ver, str):
            locked[normalize_dist(name)] = ver
    return locked
