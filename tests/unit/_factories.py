"""Builders for complete, valid objects; tests mutate one field at a time."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from crashcourt.doctor import DriverInfo
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
LINUX_WHEEL = "cp312-cp312-manylinux2014_x86_64,cp312-cp312-manylinux_2_17_x86_64"
WINDOWS_WHEEL = "cp312-cp312-win_amd64"
GOOD_DRIVER = DriverInfo(
    impl="binary", libpq=180006, libpq_build=180006, cancel_safe=True, wheel=LINUX_WHEEL
)


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
        psycopg_wheel=LINUX_WHEEL,
        libpq_version="18.6",
        libpq_build_version="18.6",
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
    running: Mapping[str, Sequence[str]] | Exception = field(
        default_factory=lambda: {s: [i] for s, i in GOOD_RUNNING.items()}
    )
    local_ids: Mapping[str, str] = field(default_factory=lambda: dict(GOOD_IMAGE_IDS))
    pg: Mapping[str, str] | Exception = field(default_factory=lambda: dict(GOOD_PG))
    toxiproxy: str | Exception = "2.9.0"
    installed: Mapping[str, str | None] = field(default_factory=lambda: dict(GOOD_VERSIONS))
    locked: Mapping[str, str] | None = field(default_factory=lambda: dict(GOOD_VERSIONS))
    lock_sha: str | None = "f" * 64
    pin: str | Exception | None = "3.12.7"
    driver: DriverInfo | Exception = GOOD_DRIVER
    git: tuple[str | None, bool] = ("0" * 40, False)
    runner_env: str = "local"
    #: Names of probes that were called, so tests can check what doctor skipped.
    calls: list[str] = field(default_factory=lambda: list[str]())

    def docker_version(self) -> str | None:
        return self.docker

    def docker_kernel(self) -> str | None:
        return self.kernel

    def compose_version(self) -> str | None:
        return self.compose

    def compose_images(self) -> Mapping[str, str] | None:
        self.calls.append("compose_images")
        return self.images

    def service_states(self) -> Mapping[str, str]:
        self.calls.append("service_states")
        return self.states

    def running_image_ids(self) -> Mapping[str, Sequence[str]]:
        self.calls.append("running_image_ids")
        if isinstance(self.running, Exception):
            raise self.running
        return self.running

    def image_id(self, ref: str) -> str | None:
        self.calls.append("image_id")
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
        if isinstance(self.pin, Exception):
            raise self.pin
        return self.pin

    def driver_info(self) -> DriverInfo:
        if isinstance(self.driver, Exception):
            raise self.driver
        return self.driver

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
