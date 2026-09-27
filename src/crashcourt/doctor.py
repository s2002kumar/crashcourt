"""`crashcourt doctor`: verify the pinned environment before any run (T-01).

Every check is independent: one failure never hides another, and no probe error crashes the report.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from crashcourt.manifest import (
    DIGEST_MARKER,
    PINNED_DISTRIBUTIONS,
    POSTGRES_SETTINGS,
    REQUIRED_SERVICES,
    Manifest,
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
    def compose_version(self) -> str | None: ...
    def compose_images(self) -> Mapping[str, str] | None: ...
    def service_states(self) -> Mapping[str, str]: ...
    def postgres_settings(self, names: Iterable[str]) -> Mapping[str, str]: ...
    def toxiproxy_version(self) -> str: ...
    def installed_versions(self, dists: Iterable[str]) -> Mapping[str, str | None]: ...
    def locked_versions(self) -> Mapping[str, str] | None: ...
    def lockfile_sha256(self) -> str | None: ...
    def git_state(self) -> tuple[str | None, bool]: ...
    def python_info(self) -> tuple[str, str]: ...
    def platform_info(self) -> tuple[str, str, str]: ...
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

    images = dict(probes.compose_images() or {})
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
    locked = probes.locked_versions()
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
    if all(check.ok for check in checks):
        py_version, py_impl = probes.python_info()
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
            lockfile_sha256=probes.lockfile_sha256() or "",
            packages={d: installed.get(d) or "" for d in PINNED_DISTRIBUTIONS},
            docker_version=docker or "",
            compose_version=compose or "",
            images={s: images[s] for s in REQUIRED_SERVICES},
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
