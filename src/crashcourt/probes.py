"""Real system probes for `crashcourt doctor`.

Unit tests use fakes; this module touches the world.
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import os
import platform
import subprocess
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from crashcourt.doctor import DriverInfo
from crashcourt.manifest import parse_uv_lock, sha256_file

#: Direct connection to the target database: the oracle path, never through Toxiproxy
#: (the oracle path).
DEFAULT_PG_DSN = "postgresql://crashcourt:crashcourt@127.0.0.1:55432/crashcourt"
DEFAULT_TOXIPROXY_URL = "http://127.0.0.1:8474"


def _compose_ps_items(raw: str) -> list[dict[str, object]]:
    """Parse both shapes of `docker compose ps --format json`: an array, or an object per line."""
    raw = raw.strip()
    if not raw:
        return []
    try:
        items: list[Any] = (
            json.loads(raw)
            if raw.startswith("[")
            else [json.loads(line) for line in raw.splitlines() if line.strip()]
        )
    except json.JSONDecodeError:
        return []  # unreadable output reads as no services; doctor reports them missing
    return [cast("dict[str, object]", item) for item in items if isinstance(item, dict)]


def parse_compose_ps(raw: str) -> dict[str, str]:
    """Map service -> health (if it has a healthcheck) or state."""
    states: dict[str, str] = {}
    for fields in _compose_ps_items(raw):
        service = str(fields.get("Service") or "")
        health = str(fields.get("Health") or "")
        state = str(fields.get("State") or "")
        if service:
            states[service] = health or state
    return states


def parse_compose_containers(raw: str) -> dict[str, list[str]]:
    """Map service -> its container IDs (more than one when the service is scaled)."""
    containers: dict[str, list[str]] = {}
    for fields in _compose_ps_items(raw):
        service = str(fields.get("Service") or "")
        container = str(fields.get("ID") or "")
        if service and container:
            containers.setdefault(service, []).append(container)
    return containers


def parse_wheel_tags(text: str) -> list[str]:
    """The `Tag:` lines of a wheel's WHEEL metadata file, sorted."""
    return sorted(
        line.partition(":")[2].strip() for line in text.splitlines() if line.startswith("Tag:")
    )


def read_python_pin(text: str) -> str | None:
    """The first version in a .python-version file; uv allows comments and blank lines."""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def runner_kind(env: Mapping[str, str]) -> str:
    """`local`, or the RUNNER_ENVIRONMENT GitHub Actions sets (`github-hosted`, `self-hosted`).

    Any other CI system is `unknown`, which the manifest rejects rather than calling it local.
    """
    if env.get("GITHUB_ACTIONS") == "true":
        return env.get("RUNNER_ENVIRONMENT", "") or "unknown"
    if env.get("CI", "").lower() in {"true", "1"} or any(env.get(k) for k in _OTHER_CI_VARIABLES):
        return "unknown"
    return "local"


#: Variables other CI systems set even when they do not set CI=true (Azure Pipelines, Jenkins,
#: TeamCity, and others).
_OTHER_CI_VARIABLES: tuple[str, ...] = (
    "TF_BUILD",
    "JENKINS_URL",
    "TEAMCITY_VERSION",
    "BUILDKITE",
    "GITLAB_CI",
    "CIRCLECI",
)


class SystemProbes:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pg_dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
        self.toxiproxy_url = os.environ.get("CRASHCOURT_TOXIPROXY_URL", DEFAULT_TOXIPROXY_URL)

    def _run(self, *args: str) -> str | None:
        try:
            done = subprocess.run(
                args,
                cwd=self.root,
                check=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",  # Docker prints UTF-8; never let an odd byte crash doctor
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return (done.stdout or "").strip()

    def docker_version(self) -> str | None:
        return self._run("docker", "version", "--format", "{{.Server.Version}}") or None

    def docker_kernel(self) -> str | None:
        return self._run("docker", "version", "--format", "{{.Server.KernelVersion}}") or None

    def compose_version(self) -> str | None:
        return self._run("docker", "compose", "version", "--short") or None

    def compose_images(self) -> Mapping[str, str] | None:
        raw = self._run("docker", "compose", "config", "--format", "json")
        if not raw:
            return None
        try:
            services: Any = json.loads(raw).get("services")
        except (json.JSONDecodeError, AttributeError):
            return None
        if not isinstance(services, dict):
            return None
        return {
            str(name): str(cast("dict[str, object]", svc).get("image", ""))
            if isinstance(svc, dict)
            else ""
            for name, svc in cast("dict[object, object]", services).items()
        }

    def service_states(self) -> Mapping[str, str]:
        return parse_compose_ps(
            self._run("docker", "compose", "ps", "--all", "--format", "json") or ""
        )

    def running_image_ids(self) -> Mapping[str, Sequence[str]]:
        containers = parse_compose_containers(
            self._run("docker", "compose", "ps", "--format", "json") or ""
        )
        found: dict[str, list[str]] = {}
        for service, ids in containers.items():
            images: set[str] = set()
            for container in ids:
                image = self._run(
                    "docker", "inspect", "--type", "container", "--format", "{{.Image}}", container
                )
                # A container that cannot be inspected must not vanish from the comparison.
                images.add(image or f"uninspectable container {container}")
            found[service] = sorted(images)
        return found

    def image_id(self, ref: str) -> str | None:
        return self._run("docker", "image", "inspect", "--format", "{{.Id}}", ref) or None

    def postgres_settings(self, names: Iterable[str]) -> Mapping[str, str]:
        import psycopg  # lazy: unit tests never need a database driver

        with psycopg.connect(self.pg_dsn, connect_timeout=5, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)", (list(names),)
            ).fetchall()
        return {str(name): str(setting) for name, setting in rows}

    def toxiproxy_version(self) -> str:
        url = self.toxiproxy_url.rstrip("/") + "/version"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode().strip()
        try:
            data: Any = json.loads(body)
        except json.JSONDecodeError:
            return body
        if isinstance(data, dict):
            return str(cast("dict[str, object]", data).get("version", body))
        return str(data)

    def installed_versions(self, dists: Iterable[str]) -> Mapping[str, str | None]:
        found: dict[str, str | None] = {}
        for dist in dists:
            try:
                found[dist] = importlib.metadata.version(dist)
            except importlib.metadata.PackageNotFoundError:
                found[dist] = None
        return found

    def locked_versions(self) -> Mapping[str, str] | None:
        path = self.root / "uv.lock"
        return parse_uv_lock(path.read_text(encoding="utf-8")) if path.exists() else None

    def lockfile_sha256(self) -> str | None:
        path = self.root / "uv.lock"
        return sha256_file(path) if path.exists() else None

    def clause_registry(self) -> tuple[int, str]:
        from crashcourt.clauses import ClauseRegistry, registry_sha256

        root = self.root / "clauses"
        return len(ClauseRegistry.load(root)), registry_sha256(root)

    def spec_provenance(self) -> tuple[str, bool | None]:
        """The recorded spec hash, and whether the local copy of the spec matches it (None when
        this checkout has no copy, as in CI or a worktree)."""
        record: Any = json.loads((self.root / "spec-provenance.json").read_text(encoding="utf-8"))
        digest = str(record["sha256"])
        local = self.root / "private" / "spec" / str(record["file"])
        return digest, (sha256_file(local) == digest) if local.is_file() else None

    def python_pin(self) -> str | None:
        path = self.root / ".python-version"
        return read_python_pin(path.read_text(encoding="utf-8")) if path.exists() else None

    def driver_info(self) -> DriverInfo:
        from psycopg import capabilities, pq  # lazy: unit tests never need a database driver

        try:
            wheel = importlib.metadata.distribution("psycopg-binary").read_text("WHEEL") or ""
        except importlib.metadata.PackageNotFoundError:
            wheel = ""
        return DriverInfo(
            impl=pq.__impl__,
            libpq=pq.version(),
            libpq_build=pq.__build_version__,
            cancel_safe=capabilities.has_cancel_safe(),
            wheel=",".join(parse_wheel_tags(wheel)),
        )

    def git_state(self) -> tuple[str | None, bool]:
        sha = self._run("git", "rev-parse", "HEAD")
        if not sha:
            return None, False
        porcelain = self._run("git", "status", "--porcelain")
        return sha, porcelain is None or porcelain != ""  # unknown counts as dirty

    def python_info(self) -> tuple[str, str]:
        return platform.python_version(), platform.python_implementation()

    def platform_info(self) -> tuple[str, str, str]:
        return platform.system(), platform.release(), platform.machine()

    def runner(self) -> str:
        return runner_kind(os.environ)

    def now_utc(self) -> str:
        return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
