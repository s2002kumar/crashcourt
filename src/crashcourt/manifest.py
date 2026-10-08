"""The environment manifest every result is stamped with (spec sections 9 and 13, control C7)."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 1

#: Distributions under test or on the persistence path; their versions are part of every result.
#: Every langgraph* and psycopg* distribution in uv.lock belongs here (a unit test enforces it).
PINNED_DISTRIBUTIONS: tuple[str, ...] = (
    "langgraph",
    "langgraph-checkpoint",
    "langgraph-checkpoint-postgres",
    "langgraph-checkpoint-sqlite",
    "langgraph-prebuilt",
    "langgraph-sdk",
    "psycopg",
    "psycopg-binary",
    "psycopg-pool",
)

#: psycopg 3.3.6 bounds the wait after interrupting a query only with libpq 17 or newer
#: (psycopg#1371); that fix is the reason for the 3.3.6 pin.
MIN_LIBPQ_MAJOR = 17

#: psycopg's implementations, as reported by `psycopg.pq.__impl__`.
PSYCOPG_IMPLS: frozenset[str] = frozenset({"c", "binary", "python"})

#: Where doctor ran: on a developer machine, or on a GitHub Actions runner of either kind.
RUNNERS: frozenset[str] = frozenset({"local", "github-hosted", "self-hosted"})

#: Postgres settings recorded in every manifest.
#: track_commit_timestamp must be "on" (spec section 8).
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
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
KERNEL_RELEASE = re.compile(r"\d+\.\d+")
LIBPQ_VERSION = re.compile(r"(\d+)\.(\d+)")

_TEXT_FIELDS: tuple[str, ...] = (
    "generated_at",
    "python_version",
    "python_implementation",
    "os_system",
    "machine",
    "runner",
    "docker_version",
    "docker_kernel",
    "compose_version",
    "psycopg_impl",
    "libpq_version",
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
    runner: str
    lockfile_sha256: str
    packages: dict[str, str]
    psycopg_impl: str
    libpq_version: str
    docker_version: str
    #: The Docker engine's kernel: the kernel Postgres and Toxiproxy run on. On Windows and macOS
    #: it is the Linux VM's kernel, not the host's (os_release).
    docker_kernel: str
    compose_version: str
    #: The refs compose.yaml asks for, each pinned by digest.
    images: dict[str, str]
    #: The image ID each running container was created from; doctor checks it against `images`.
    image_ids: dict[str, str]
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
    if m.runner and m.runner not in RUNNERS:
        problems.append(f"runner must be one of {sorted(RUNNERS)}, got {m.runner}")
    if m.docker_kernel and not KERNEL_RELEASE.match(m.docker_kernel):
        problems.append(f"docker_kernel is not a kernel release: {m.docker_kernel}")
    if m.psycopg_impl and m.psycopg_impl not in PSYCOPG_IMPLS:
        problems.append(
            f"psycopg_impl must be one of {sorted(PSYCOPG_IMPLS)}, got {m.psycopg_impl}"
        )
    if m.libpq_version:
        major = libpq_major(m.libpq_version)
        if major is None:
            problems.append(f"libpq_version is not major.minor: {m.libpq_version}")
        elif major < MIN_LIBPQ_MAJOR:
            problems.append(
                f"libpq {m.libpq_version} is older than {MIN_LIBPQ_MAJOR}; "
                "psycopg's bounded wait after an interrupt needs it"
            )
    for service in REQUIRED_SERVICES:
        ref = m.images.get(service, "")
        if DIGEST_MARKER not in ref:
            problems.append(f"image not pinned by digest: {service} ({ref or 'missing'})")
        image_id = m.image_ids.get(service, "")
        if not IMAGE_ID.fullmatch(image_id):
            problems.append(f"running image ID missing: {service} ({image_id or 'missing'})")
    for setting in POSTGRES_SETTINGS:
        if not m.postgres.get(setting):
            problems.append(f"postgres setting missing: {setting}")
    tct = m.postgres.get("track_commit_timestamp")
    if tct and tct != "on":
        problems.append(f"track_commit_timestamp must be on, got {tct}")
    return problems


def libpq_major(version: str) -> int | None:
    """`18.4` -> 18; None when the text is not `major.minor`."""
    match = LIBPQ_VERSION.fullmatch(version)
    return int(match.group(1)) if match else None


def format_libpq_version(number: int) -> str:
    """`psycopg.pq.version()` encodes libpq 10 and later as major * 10000 + minor."""
    return f"{number // 10000}.{number % 10000}"


def pinned_view(m: Manifest) -> dict[str, Any]:
    """Fields that must match for two runs to be aggregated (C7).

    Host details (OS, machine, runner, Docker and its kernel, image IDs) are recorded only. libpq is
    compared by major version: psycopg-binary bundles a different libpq minor in its Windows and
    Linux wheels, so the full version cannot match between a Windows host and a Linux runner.
    """
    return {
        "schema_version": m.schema_version,
        "python_version": m.python_version,
        "python_implementation": m.python_implementation,
        "lockfile_sha256": m.lockfile_sha256,
        "packages": dict(sorted(m.packages.items())),
        "psycopg_impl": m.psycopg_impl,
        "libpq_major": libpq_major(m.libpq_version),
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
        fields = cast("dict[str, object]", entry)
        name, ver = fields.get("name"), fields.get("version")
        if isinstance(name, str) and isinstance(ver, str):
            locked[normalize_dist(name)] = ver
    return locked
