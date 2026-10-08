"""The environment manifest every result is stamped with (spec sections 9 and 13, control C7)."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass, fields
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

#: psycopg 3.3.6 bounds the wait after interrupting a query (psycopg#1371), the reason for that
#: pin, only through `cancel_safe()`, which needs libpq 17 or newer both at runtime and at build
#: time (`psycopg.capabilities.has_cancel_safe`).
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
    "psycopg_wheel",
    "libpq_version",
    "libpq_build_version",
    "toxiproxy_version",
)


class ManifestFormatError(ValueError):
    """The text is not a manifest of this schema."""


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
    #: The wheel tags of the installed psycopg-binary; the binary implementation's libpq comes
    #: from that wheel, and each platform's wheel bundles its own libpq build.
    psycopg_wheel: str
    #: The libpq psycopg loaded, and the libpq its wrapper was built against.
    libpq_version: str
    libpq_build_version: str
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
        """Parse a manifest; ManifestFormatError unless it has exactly this schema's fields."""
        data: Any = json.loads(text)
        if not isinstance(data, dict):
            raise ManifestFormatError("a manifest is a JSON object")
        record = cast("dict[str, Any]", data)
        version = record.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ManifestFormatError(f"schema_version {version!r}, expected {SCHEMA_VERSION}")
        names = {field.name for field in fields(cls)}
        missing = sorted(names - record.keys())
        unknown = sorted(record.keys() - names)
        if missing or unknown:
            raise ManifestFormatError(
                f"not a schema {SCHEMA_VERSION} manifest: missing {missing}, unknown {unknown}"
            )
        wrong = sorted(name for name in names if not _has_field_type(name, record[name]))
        if wrong:
            raise ManifestFormatError(f"fields with the wrong type: {wrong}")
        return cls(**record)


#: JSON types of the fields that are not strings.
_FIELD_TYPES: dict[str, str] = {
    "git_dirty": "bool",
    "schema_version": "int",
    "packages": "map",
    "images": "map",
    "image_ids": "map",
    "postgres": "map",
}


def _has_field_type(name: str, value: object) -> bool:
    kind = _FIELD_TYPES.get(name, "str")
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return type(value) is int
    if kind == "map":
        if not isinstance(value, dict):
            return False
        items = cast("dict[object, object]", value).items()
        return all(isinstance(k, str) and isinstance(v, str) for k, v in items)
    return isinstance(value, str)


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
        problems.append(f"docker_kernel does not start with a version: {m.docker_kernel}")
    if m.psycopg_impl and m.psycopg_impl not in PSYCOPG_IMPLS:
        problems.append(
            f"psycopg_impl must be one of {sorted(PSYCOPG_IMPLS)}, got {m.psycopg_impl}"
        )
    for name in ("libpq_version", "libpq_build_version"):
        value: str = getattr(m, name)
        if not value:
            continue
        major = libpq_major(value)
        if major is None:
            problems.append(f"{name} is not major.minor: {value}")
        elif major < MIN_LIBPQ_MAJOR:
            problems.append(
                f"{name} {value} is older than {MIN_LIBPQ_MAJOR}; "
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
    """The fields the pins determine; `doctor --compare` fails when two manifests differ in them.

    Host details (OS, machine, runner, Docker and its kernel, image IDs) are recorded only. libpq is
    compared here by major version, at runtime and at build time; `diff_pinned` adds the full
    versions whenever the two runs could not have loaded different libpq builds (see
    `libpq_differs_by_wheel`).

    This answers "do the pins reproduce the same environment on two machines", the T-01 use of C7.
    It is not the whole rule for aggregating batch results: spec section 20 also keeps runner types
    apart, and batches from different platforms ran different libpq builds.
    """
    return {
        "schema_version": m.schema_version,
        "python_version": m.python_version,
        "python_implementation": m.python_implementation,
        "lockfile_sha256": m.lockfile_sha256,
        "packages": dict(sorted(m.packages.items())),
        "psycopg_impl": m.psycopg_impl,
        "libpq_major": libpq_major(m.libpq_version),
        "libpq_build_major": libpq_major(m.libpq_build_version),
        "images": dict(sorted(m.images.items())),
        "postgres": dict(sorted(m.postgres.items())),
        "toxiproxy_version": m.toxiproxy_version,
    }


def libpq_differs_by_wheel(a: Manifest, b: Manifest) -> bool:
    """True when both runs used psycopg-binary's bundled libpq from different platform wheels.

    psycopg-binary 3.3.6 bundles libpq 18.4 in its Windows wheel and 18.6 in its Linux wheel, so
    identical pins can load different libpq builds on different platforms. With the same wheel, or
    with a libpq from the system (the c and python implementations), the pins leave no such room.
    """
    return (
        a.psycopg_impl == b.psycopg_impl == "binary"
        and bool(a.psycopg_wheel)
        and bool(b.psycopg_wheel)
        and a.psycopg_wheel != b.psycopg_wheel
    )


def diff_pinned(a: Manifest, b: Manifest) -> list[str]:
    """Human-readable differences that fail a comparison of two manifests."""
    va, vb = pinned_view(a), pinned_view(b)
    diffs = [f"{key}: {va[key]!r} != {vb[key]!r}" for key in va if va[key] != vb[key]]
    if not libpq_differs_by_wheel(a, b):
        for name in ("libpq_version", "libpq_build_version"):
            if getattr(a, name) != getattr(b, name):
                diffs.append(f"{name}: {getattr(a, name)!r} != {getattr(b, name)!r}")
    return diffs


def platform_notes(a: Manifest, b: Manifest) -> list[str]:
    """Differences the pins allow across platforms: reported with every comparison, never hidden."""
    if not libpq_differs_by_wheel(a, b):
        return []
    if (a.libpq_version, a.libpq_build_version) == (b.libpq_version, b.libpq_build_version):
        return []
    same_majors = all(
        libpq_major(getattr(a, name)) == libpq_major(getattr(b, name))
        for name in ("libpq_version", "libpq_build_version")
    )
    return [
        f"libpq {a.libpq_version} (built {a.libpq_build_version}, wheel {a.psycopg_wheel}) vs "
        f"{b.libpq_version} (built {b.libpq_build_version}, wheel {b.psycopg_wheel}): "
        "each platform's psycopg-binary wheel bundles its own libpq; "
        + ("majors match" if same_majors else "majors differ")
    ]


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
        item = cast("dict[str, object]", entry)
        name, ver = item.get("name"), item.get("version")
        if isinstance(name, str) and isinstance(ver, str):
            locked[normalize_dist(name)] = ver
    return locked
