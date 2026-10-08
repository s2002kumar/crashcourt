"""Builders for complete, valid objects; tests mutate one field at a time."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

from crashcourt.manifest import PINNED_DISTRIBUTIONS, POSTGRES_SETTINGS, Manifest

D1 = "sha256:" + "a" * 64
D2 = "sha256:" + "b" * 64
GOOD_IMAGES = {
    "postgres": f"postgres:18@{D1}",
    "toxiproxy": f"ghcr.io/shopify/toxiproxy:2.9.0@{D2}",
}
GOOD_PG = {
    "server_version": "18.3",
    "fsync": "on",
    "synchronous_commit": "on",
    "track_commit_timestamp": "on",
    "default_transaction_isolation": "read committed",
    "max_connections": "100",
}
GOOD_VERSIONS = {d: f"1.0.{i}" for i, d in enumerate(PINNED_DISTRIBUTIONS)}
ID_PG = "sha256:" + "c" * 64
ID_TOXI = "sha256:" + "d" * 64
GOOD_IMAGE_IDS = {GOOD_IMAGES["postgres"]: ID_PG, GOOD_IMAGES["toxiproxy"]: ID_TOXI}
GOOD_RUNNING = {"postgres": ID_PG, "toxiproxy": ID_TOXI}


def make_manifest(**changes: object) -> Manifest:
    base = Manifest(
        generated_at="2026-09-26T12:00:00+00:00",
        git_sha="0" * 40,
        git_dirty=False,
        python_version="3.12.7",
        python_implementation="CPython",
        os_system="Linux",
        os_release="6.8.0",
        machine="x86_64",
        runner="local",
        lockfile_sha256="f" * 64,
        packages=dict(GOOD_VERSIONS),
        psycopg_impl="binary",
        libpq_version="18.4",
        docker_version="27.3.1",
        docker_kernel="6.8.0-45-generic",
        compose_version="2.29.7",
        images=dict(GOOD_IMAGES),
        image_ids=dict(GOOD_RUNNING),
        postgres=dict(GOOD_PG),
        toxiproxy_version="2.9.0",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@dataclass
class FakeProbes:
    """Every probe answers like a healthy environment unless a test overrides it."""

    docker: str | None = "27.3.1"
    kernel: str | None = "6.8.0-45-generic"
    compose: str | None = "2.29.7"
    images: Mapping[str, str] | None = field(default_factory=lambda: dict(GOOD_IMAGES))
    states: Mapping[str, str] = field(
        default_factory=lambda: {"postgres": "healthy", "toxiproxy": "running"}
    )
    running: Mapping[str, str] = field(default_factory=lambda: dict(GOOD_RUNNING))
    local_ids: Mapping[str, str] = field(default_factory=lambda: dict(GOOD_IMAGE_IDS))
    pg: Mapping[str, str] | Exception = field(default_factory=lambda: dict(GOOD_PG))
    toxiproxy: str | Exception = "2.9.0"
    installed: Mapping[str, str | None] = field(default_factory=lambda: dict(GOOD_VERSIONS))
    locked: Mapping[str, str] | None = field(default_factory=lambda: dict(GOOD_VERSIONS))
    lock_sha: str | None = "f" * 64
    pin: str | None = "3.12.7"
    psycopg: tuple[str, int] | Exception = ("binary", 180004)
    git: tuple[str | None, bool] = ("0" * 40, False)
    runner_env: str = "local"

    def docker_version(self) -> str | None:
        return self.docker

    def docker_kernel(self) -> str | None:
        return self.kernel

    def compose_version(self) -> str | None:
        return self.compose

    def compose_images(self) -> Mapping[str, str] | None:
        return self.images

    def service_states(self) -> Mapping[str, str]:
        return self.states

    def running_image_ids(self) -> Mapping[str, str]:
        return self.running

    def image_id(self, ref: str) -> str | None:
        return self.local_ids.get(ref)

    def postgres_settings(self, names: Iterable[str]) -> Mapping[str, str]:
        if isinstance(self.pg, Exception):
            raise self.pg
        wanted = set(names)
        assert wanted == set(POSTGRES_SETTINGS)
        return {k: v for k, v in self.pg.items() if k in wanted}

    def toxiproxy_version(self) -> str:
        if isinstance(self.toxiproxy, Exception):
            raise self.toxiproxy
        return self.toxiproxy

    def installed_versions(self, dists: Iterable[str]) -> Mapping[str, str | None]:
        return {d: self.installed.get(d) for d in dists}

    def locked_versions(self) -> Mapping[str, str] | None:
        return self.locked

    def lockfile_sha256(self) -> str | None:
        return self.lock_sha

    def python_pin(self) -> str | None:
        return self.pin

    def psycopg_info(self) -> tuple[str, int]:
        if isinstance(self.psycopg, Exception):
            raise self.psycopg
        return self.psycopg

    def git_state(self) -> tuple[str | None, bool]:
        return self.git

    def python_info(self) -> tuple[str, str]:
        return "3.12.7", "CPython"

    def platform_info(self) -> tuple[str, str, str]:
        return "Linux", "6.8.0", "x86_64"

    def runner(self) -> str:
        return self.runner_env

    def now_utc(self) -> str:
        return "2026-09-26T12:00:00+00:00"
