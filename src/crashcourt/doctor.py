"""`crashcourt doctor`: verify the pinned environment before any run (T-01).

Every check is independent: one failure never hides another, and no probe error crashes the report.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from crashcourt.manifest import (
    DIGEST_MARKER,
    MIN_LIBPQ_MAJOR,
    PINNED_DISTRIBUTIONS,
    POSTGRES_SETTINGS,
    REQUIRED_SERVICES,
    Manifest,
    format_libpq_version,
    normalize_dist,
    validate_manifest,
)

#: Accepted compose states per service. Postgres has a healthcheck; Toxiproxy's image has no shell.
ACCEPTED_STATES: Mapping[str, frozenset[str]] = {
    "postgres": frozenset({"healthy"}),
    "toxiproxy": frozenset({"healthy", "running"}),
}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    remedy: str = ""


@dataclass(frozen=True)
class DriverInfo:
    """What psycopg loaded. libpq versions use libpq's encoding: 180004 is 18.4."""

    impl: str
    libpq: int
    libpq_build: int
    #: `psycopg.capabilities.has_cancel_safe()`: the bounded cancel the 3.3.6 pin is for.
    cancel_safe: bool
    #: The installed psycopg-binary wheel's tags, comma-separated.
    wheel: str


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[Check, ...]
    manifest: Manifest | None

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.ok)


class Probes(Protocol):
    def docker_version(self) -> str | None: ...
    def docker_kernel(self) -> str | None: ...
    def compose_version(self) -> str | None: ...
    def compose_images(self) -> Mapping[str, str] | None: ...
    def service_states(self) -> Mapping[str, str]: ...
    def running_image_ids(self) -> Mapping[str, Sequence[str]]: ...
    def image_id(self, ref: str) -> str | None: ...
    def postgres_settings(self, names: Iterable[str]) -> Mapping[str, str]: ...
    def toxiproxy_version(self) -> str: ...
    def installed_versions(self, dists: Iterable[str]) -> Mapping[str, str | None]: ...
    def locked_versions(self) -> Mapping[str, str] | None: ...
    def lockfile_sha256(self) -> str | None: ...
    def clause_registry(self) -> tuple[int, str]: ...
    def python_pin(self) -> str | None: ...
    def driver_info(self) -> DriverInfo: ...
    def git_state(self) -> tuple[str | None, bool]: ...
    def python_info(self) -> tuple[str, str]: ...
    def platform_info(self) -> tuple[str, str, str]: ...
    def runner(self) -> str: ...
    def now_utc(self) -> str: ...


def _check(name: str, ok: bool, detail: str, remedy: str) -> Check:
    return Check(name, ok, detail, "" if ok else remedy)


def run_doctor(probes: Probes) -> DoctorReport:
    checks: list[Check] = []

    docker = probes.docker_version()
    checks.append(
        _check("docker daemon", docker is not None, docker or "not reachable", "Start Docker.")
    )
    compose = probes.compose_version()
    checks.append(
        _check("docker compose", compose is not None, compose or "not found", "Install Compose v2.")
    )

    images: dict[str, str] = {}
    running: dict[str, tuple[str, ...]] = {}
    if docker is None:
        # Every probe below would wait out its own timeout on a daemon that does not answer.
        skipped = "skipped: docker daemon not reachable"
        for name in ("images pinned by digest", "compose services up", "running images match pins"):
            checks.append(_check(name, False, skipped, "Start Docker."))
    else:
        images = dict(probes.compose_images() or {})
        running = _docker_checks(probes, images, checks)

    pg: dict[str, str] = {}
    try:
        pg = dict(probes.postgres_settings(POSTGRES_SETTINGS))
    except Exception as exc:  # noqa: BLE001 - any connection failure is a failed check, not a crash
        remedy = (
            "Run `uv sync --locked --group dev` (the database driver is not installed)."
            if isinstance(exc, ImportError)
            else "Run `docker compose up -d --wait`; check CRASHCOURT_PG_DSN."
        )
        checks.append(_check("postgres reachable", False, f"{type(exc).__name__}: {exc}", remedy))
    else:
        tct = pg.get("track_commit_timestamp")
        checks.append(
            _check(
                "postgres track_commit_timestamp",
                tct == "on",
                f"track_commit_timestamp={tct}",
                "Start postgres with `-c track_commit_timestamp=on` (compose.yaml does this).",
            )
        )

    toxiproxy: str | None = None
    try:
        toxiproxy = probes.toxiproxy_version()
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _check(
                "toxiproxy api",
                False,
                f"{type(exc).__name__}: {exc}",
                "Run `docker compose up -d --wait`; check CRASHCOURT_TOXIPROXY_URL.",
            )
        )
    else:
        checks.append(_check("toxiproxy api", True, f"version {toxiproxy}", ""))

    installed = dict(probes.installed_versions(PINNED_DISTRIBUTIONS))
    try:
        locked = probes.locked_versions()
    except Exception as exc:  # noqa: BLE001 - an unreadable lock is a failed check, not a crash
        checks.append(
            _check(
                "uv.lock readable",
                False,
                f"{type(exc).__name__}: {exc}",
                "Restore uv.lock (`git checkout -- uv.lock`).",
            )
        )
    else:
        if locked is None:
            checks.append(_check("uv.lock present", False, "uv.lock not found", "Run `uv lock`."))
        else:
            mismatches = [
                f"{d}: installed {installed.get(d)} vs locked {locked.get(normalize_dist(d))}"
                for d in PINNED_DISTRIBUTIONS
                if installed.get(d) is None or installed.get(d) != locked.get(normalize_dist(d))
            ]
            checks.append(
                _check(
                    "installed versions match uv.lock",
                    not mismatches,
                    "all match" if not mismatches else "; ".join(mismatches),
                    "Run `uv sync --locked --group dev`.",
                )
            )

    registry_digest = ""
    try:
        clause_count, registry_digest = probes.clause_registry()
    except Exception as exc:  # noqa: BLE001 - an invalid registry is a failed check, not a crash
        checks.append(
            _check(
                "clause registry valid",
                False,
                f"{type(exc).__name__}: {str(exc).replace(chr(10), '; ')}",
                "Run `uv run crashcourt clauses check` and fix the listed clauses.",
            )
        )
    else:
        checks.append(
            _check(
                "clause registry valid",
                True,
                f"{clause_count} clauses, sha256 {registry_digest[:12]}",
                "",
            )
        )

    py_version, py_impl = probes.python_info()
    try:
        pin = probes.python_pin()
    except Exception as exc:  # noqa: BLE001 - an unreadable pin file is a failed check
        checks.append(
            _check(
                "python matches .python-version",
                False,
                f"{type(exc).__name__}: {exc}",
                "Make .python-version readable; it names the interpreter to use.",
            )
        )
    else:
        checks.append(
            _check(
                "python matches .python-version",
                pin is not None and py_version == pin,
                f"running {py_version}, pinned {pin}" if pin else ".python-version not found",
                "Run `uv sync --locked --group dev`; uv installs the pinned interpreter.",
            )
        )

    driver: DriverInfo | None = None
    driver_remedy = (
        f"psycopg bounds the wait after an interrupt only with libpq {MIN_LIBPQ_MAJOR} or newer, "
        "at runtime and at build time; run `uv sync --locked --group dev` for the pinned "
        "psycopg-binary."
    )
    try:
        driver = probes.driver_info()
    except Exception as exc:  # noqa: BLE001 - a broken driver is a failed check, not a crash
        checks.append(
            _check("psycopg cancel_safe", False, f"{type(exc).__name__}: {exc}", driver_remedy)
        )
    else:
        checks.append(
            _check(
                "psycopg cancel_safe",
                driver.cancel_safe,
                f"{driver.impl} implementation, libpq {format_libpq_version(driver.libpq)}, "
                f"built against {format_libpq_version(driver.libpq_build)}"
                + ("" if driver.cancel_safe else "; cancel_safe() unavailable"),
                driver_remedy,
            )
        )

    sha, dirty = probes.git_state()
    checks.append(
        _check(
            "git commit",
            sha is not None,
            (f"{sha} (dirty)" if dirty else f"{sha}") if sha else "not a git checkout",
            "Run inside a git clone with at least one commit.",
        )
    )

    manifest: Manifest | None = None
    if driver is not None and all(check.ok for check in checks):
        system, release, machine = probes.platform_info()
        candidate = Manifest(
            generated_at=probes.now_utc(),
            git_sha=sha or "",
            git_dirty=dirty,
            python_version=py_version,
            python_implementation=py_impl,
            os_system=system,
            os_release=release,
            machine=machine,
            runner=probes.runner(),
            lockfile_sha256=_lockfile_sha256(probes),
            clause_registry_sha256=registry_digest,
            packages={d: installed.get(d) or "" for d in PINNED_DISTRIBUTIONS},
            psycopg_impl=driver.impl,
            psycopg_wheel=driver.wheel,
            libpq_version=format_libpq_version(driver.libpq),
            libpq_build_version=format_libpq_version(driver.libpq_build),
            docker_version=docker or "",
            docker_kernel=probes.docker_kernel() or "",
            compose_version=compose or "",
            images={s: images[s] for s in REQUIRED_SERVICES},
            image_ids={s: running[s][0] for s in REQUIRED_SERVICES},
            postgres=pg,
            toxiproxy_version=toxiproxy or "",
        )
        problems = validate_manifest(candidate)
        checks.append(
            _check(
                "manifest complete",
                not problems,
                "complete" if not problems else "; ".join(problems),
                "Fix the listed fields; src/crashcourt/manifest.py defines the required set.",
            )
        )
        manifest = candidate if not problems else None

    return DoctorReport(tuple(checks), manifest)


def _lockfile_sha256(probes: Probes) -> str:
    """Empty when the lock cannot be hashed; validation then refuses the manifest."""
    try:
        return probes.lockfile_sha256() or ""
    except OSError:
        return ""


def _docker_checks(
    probes: Probes, images: Mapping[str, str], checks: list[Check]
) -> dict[str, tuple[str, ...]]:
    """Pinned refs, service health, and running images; returns each service's image IDs."""
    unpinned = [s for s in REQUIRED_SERVICES if DIGEST_MARKER not in images.get(s, "")]
    checks.append(
        _check(
            "images pinned by digest",
            not unpinned,
            "all pinned"
            if not unpinned
            else "unpinned: " + ", ".join(f"{s}={images.get(s) or 'missing'}" for s in unpinned),
            "Run `python scripts/pin_images.py`, then commit compose.yaml.",
        )
    )

    states = dict(probes.service_states())
    bad = [
        f"{s}={states.get(s) or 'missing'}"
        for s in REQUIRED_SERVICES
        if states.get(s) not in ACCEPTED_STATES[s]
    ]
    checks.append(
        _check(
            "compose services up",
            not bad,
            "postgres healthy, toxiproxy running" if not bad else "; ".join(bad),
            "Run `docker compose up -d --wait`.",
        )
    )

    # compose.yaml says what should run; the containers say what does. Compare image IDs.
    remedy = "Run `docker compose up -d --wait` to recreate containers from the pinned images."
    try:
        running = {s: tuple(ids) for s, ids in probes.running_image_ids().items()}
    except Exception as exc:  # noqa: BLE001 - a probe failure is a failed check, not a crash
        checks.append(
            _check("running images match pins", False, f"{type(exc).__name__}: {exc}", remedy)
        )
        return {}
    stale: list[str] = []
    for service in REQUIRED_SERVICES:
        ref = images.get(service, "")
        want = probes.image_id(ref) if ref else None
        have = running.get(service, ())
        if not have:
            stale.append(f"{service}: no container")
        elif not ref:
            stale.append(f"{service}: no image in compose.yaml")
        elif want is None:
            stale.append(f"{service}: pinned image {ref} is not present locally")
        elif set(have) != {want}:
            stale.append(f"{service}: containers run {', '.join(have)}, pinned ref is {want}")
    checks.append(
        _check(
            "running images match pins",
            not stale,
            "all match" if not stale else "; ".join(stale),
            remedy,
        )
    )
    return running


def format_report(report: DoctorReport) -> str:
    """Plain-text report. Uses ok/FAIL, never the lab's verdict words."""
    width = max(len(check.name) for check in report.checks)
    lines: list[str] = []
    for check in report.checks:
        status = "ok  " if check.ok else "FAIL"
        lines.append(f"{status}  {check.name.ljust(width)}  {check.detail}")
        if not check.ok and check.remedy:
            lines.append(f"      {' ' * width}  -> {check.remedy}")
    lines.append("")
    lines.append(
        "doctor: all checks passed"
        if report.ok
        else f"doctor: {len(report.failures)} check(s) failed"
    )
    return "\n".join(lines)
