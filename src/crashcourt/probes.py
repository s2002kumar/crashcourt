"""Real system probes for `crashcourt doctor`. Unit tests use fakes; this module touches the world."""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import os
import platform
import subprocess
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from crashcourt.manifest import parse_uv_lock, sha256_file

#: Direct connection to the target database: the oracle path, never through Toxiproxy (spec section 7).
DEFAULT_PG_DSN = "postgresql://crashcourt:crashcourt@127.0.0.1:5432/crashcourt"
DEFAULT_TOXIPROXY_URL = "http://127.0.0.1:8474"


def parse_compose_ps(raw: str) -> dict[str, str]:
    """Map service -> health (if a healthcheck exists) or state, for both `ps --format json` shapes."""
    raw = raw.strip()
    if not raw:
        return {}
    items: list[Any] = json.loads(raw) if raw.startswith("[") else [
        json.loads(line) for line in raw.splitlines() if line.strip()
    ]
    states: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        service = str(item.get("Service") or "")  # pyright: ignore[reportUnknownMemberType]
        health = str(item.get("Health") or "")  # pyright: ignore[reportUnknownMemberType]
        state = str(item.get("State") or "")  # pyright: ignore[reportUnknownMemberType]
        if service:
            states[service] = health or state
    return states


class SystemProbes:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pg_dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
        self.toxiproxy_url = os.environ.get("CRASHCOURT_TOXIPROXY_URL", DEFAULT_TOXIPROXY_URL)

    def _run(self, *args: str) -> str | None:
        try:
            done = subprocess.run(
                args, cwd=self.root, check=True, capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip()

    def docker_version(self) -> str | None:
        return self._run("docker", "version", "--format", "{{.Server.Version}}") or None

    def compose_version(self) -> str | None:
        return self._run("docker", "compose", "version", "--short") or None

    def compose_images(self) -> Mapping[str, str] | None:
        raw = self._run("docker", "compose", "config", "--format", "json")
        if not raw:
            return None
        services: dict[str, Any] = json.loads(raw).get("services", {})
        return {name: str(svc.get("image", "")) for name, svc in services.items()}

    def service_states(self) -> Mapping[str, str]:
        return parse_compose_ps(self._run("docker", "compose", "ps", "--all", "--format", "json") or "")

    def postgres_settings(self, names: Iterable[str]) -> Mapping[str, str]:
        import psycopg  # lazy: unit tests never need a database driver

        with psycopg.connect(self.pg_dsn, connect_timeout=5, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)", (list(names),)
            ).fetchall()
        return {str(name): str(setting) for name, setting in rows}

    def toxiproxy_version(self) -> str:
        url = self.toxiproxy_url.rstrip("/") + "/version"
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - local control API
            body = response.read().decode().strip()
        try:
            data: Any = json.loads(body)
        except json.JSONDecodeError:
            return body
        if isinstance(data, dict):
            return str(data.get("version", body))  # pyright: ignore[reportUnknownMemberType]
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

    def now_utc(self) -> str:
        return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
