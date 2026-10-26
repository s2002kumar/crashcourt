"""Create a durable GitHub release from already-recorded CrashCourt Actions artifacts.

This is an archiver, not an experiment or a classifier. No formal run is retried and no
failure is reclassified. Existing published releases are never mutated. Missing artifacts,
digest mismatches or suspected secrets block publication without printing secret contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

TAG = "evidence-2026-10"
FIRST_CONTINUATION_ID = 40155579729
PINNED: dict[int, str] = {
    12057625741: "a24ae88db40258bb629a8875ff5f4b32fef34ff916e42c9d8d307cf73ad96418",
    12073633432: "500aa42cc7114333c6524024d5bd8ceaa12d2cc9b6c68111785946d5bdded529",
    12079795140: "bd8957a589aae0c946555ca6975b0c0106187891f3e4b8b66881aa35d3fc46b2",
    12108676829: "424b6074d18d7d3fe0b60f17caf6820830cb60e3b9759907955fc208cec1ae0a",
    12110919822: "acd1ba23ac5c62afb741876cfd10697d66af89f757a78d2a9473c8b3e026fd2f",
    12111920700: "a3933dc931ee16508b3796a354204034badcfd9ee335dcfcbec8898903e13767",
    12171751188: "3b803d778229f9be80dae2f7958f6f13bd856a18e7f1e238e96b7b80a0f4c3a1",
    12190079569: "53ec9a5f1323e67497ae91f2eaca22b907429e1135d893ba6e121cc6b9cd60ab",
    12584876230: "3697fb80e3f604f46561016bf772e25c00c351c8793b8dcda09b68e211419522",
}
TEXT_EXTENSIONS = {
    ".json",
    ".jsonl",
    ".txt",
    ".log",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".py",
    ".sql",
}
SCANNERS = {
    "Github credential": re.compile(r"\b(?:ghp_|gho_|ghs_|github_pat_)[A-Za-z0-9_]{15,}"),
    "API credential": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{18,}|AIza[A-Za-z0-9_-]{30,})"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "user path": re.compile(
        r"(?i)(?:[A-Za-z]:[\\/]+Users[\\/]+|/Users/[^/\s]+/|/home/(?!runner)[^/\s]+/)"
    ),
}


class ArchiveError(Exception):
    """Refuse to publish if an archive fails its invariants."""


def gh(repo: str, endpoint: str) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/{endpoint}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ArchiveError(f"GitHub metadata request refused: {endpoint}")
    response = json.loads(result.stdout)
    if not isinstance(response, dict):
        raise ArchiveError("GitHub metadata is not an object")
    return response


def digest_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def fetch_artifact(repo: str, artifact_id: int, expected: str, directory: Path) -> dict[str, Any]:
    metadata = gh(repo, f"actions/artifacts/{artifact_id}")
    name = metadata.get("name")
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]+", name) is None:
        raise ArchiveError(f"Artifact {artifact_id} has an unsafe name")
    if metadata.get("expired") is not False:
        raise ArchiveError(f"Artifact {artifact_id} has expired")
    if metadata.get("digest") != f"sha256:{expected}":
        raise ArchiveError(f"Artifact {artifact_id} does not match its pinned GitHub digest")
    target = directory / f"{name}.zip"
    if target.exists():
        raise ArchiveError(f"Duplicate archive name for artifact {artifact_id}")
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise ArchiveError("No GitHub Actions token available")
    response = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--header",
            f"Authorization: Bearer {token}",
            f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip",
            "--output",
            str(target),
        ],
        capture_output=True,
        check=False,
    )
    if response.returncode:
        raise ArchiveError(f"Artifact {artifact_id} could not be downloaded")
    actual = digest_file(target)
    if actual != expected:
        raise ArchiveError(f"Artifact {artifact_id} has a different downloaded SHA-256")
    run = metadata.get("workflow_run") or {}
    return {
        "asset": target.name,
        "github_artifact_id": artifact_id,
        "github_run_id": run.get("id"),
        "sha256": actual,
        "size_bytes": target.stat().st_size,
    }


def require_one_artifact(repo: str, run_id: int, prefix: str) -> tuple[int, str]:
    items = gh(repo, f"actions/runs/{run_id}/artifacts?per_page=100").get("artifacts", [])
    matches = [x for x in items if str(x.get("name", "")).startswith(prefix)]
    if len(matches) != 1:
        raise ArchiveError(f"Run {run_id} did not provide exactly one expected {prefix} artifact")
    selected = matches[0]
    digest = selected.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ArchiveError(f"Run {run_id} has no artifact SHA-256 digest")
    return int(selected["id"]), digest.partition(":")[2]


def find_continuation(repo: str) -> tuple[int, str]:
    runs = gh(repo, "actions/runs?per_page=100").get("workflow_runs", [])
    matches = [
        x
        for x in runs
        if x.get("name") == "mitigation-study"
        and x.get("event") == "workflow_dispatch"
        and int(x.get("id", 0)) > FIRST_CONTINUATION_ID
    ]
    if len(matches) != 1:
        raise ArchiveError(f"Expected exactly one study continuation, found {len(matches)}")
    run = matches[0]
    if run.get("status") != "completed":
        raise ArchiveError("Study continuation is not complete")
    return int(run["id"]), str(run.get("conclusion"))


def scan_contents(archive: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                name = member.filename
                path = Path(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name:
                    raise ArchiveError(f"Unsafe archive entry in {archive.name}")
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ArchiveError(f"Symlink archive entry in {archive.name}")
                if path.name.lower() in {".env", "id_rsa", "id_ed25519"}:
                    raise ArchiveError(f"Potential private file in {archive.name}")
                if member.is_dir() or path.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                if member.file_size > 8_000_000:
                    raise ArchiveError(f"Unscanned oversized text in {archive.name}")
                contents = package.read(member).decode("utf-8", errors="ignore")
                for label, pattern in SCANNERS.items():
                    if pattern.search(contents):
                        raise ArchiveError(f"{label} suspected in {archive.name}; not publishing")
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"Invalid ZIP artifact {archive.name}") from exc


def release_exists(repo: str) -> bool:
    found = subprocess.run(
        ["gh", "release", "view", TAG, "--repo", repo],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return found.returncode == 0


def publish(repo: str, bench_run: int) -> None:
    if release_exists(repo):
        raise ArchiveError("Evidence release already exists; will not replace or republish it")
    bench = gh(repo, f"actions/runs/{bench_run}")
    if bench.get("name") != "bench" or bench.get("event") != "workflow_dispatch":
        raise ArchiveError("Triggering run is not the canonical dispatched benchmark")
    if bench.get("status") != "completed":
        raise ArchiveError("Canonical benchmark has not completed")
    study_run, study_result = find_continuation(repo)
    bench_result = str(bench.get("conclusion"))

    with tempfile.TemporaryDirectory(prefix="crashcourt-evidence-") as scratch:
        directory = Path(scratch)
        records: list[dict[str, Any]] = []
        for artifact_id, expected in PINNED.items():
            records.append(fetch_artifact(repo, artifact_id, expected, directory))
        for run, prefix in (
            (study_run, f"mitigation-study-gh{study_run}"),
            (bench_run, f"bench-gh{bench_run}"),
        ):
            artifact_id, digest = require_one_artifact(repo, run, prefix)
            records.append(fetch_artifact(repo, artifact_id, digest, directory))
        for record in records:
            scan_contents(directory / str(record["asset"]))

        manifest = directory / "evidence-manifest.json"
        manifest.write_text(
            json.dumps({"version": 1, "project": "CrashCourt", "assets": records}, indent=2) + "\n",
            encoding="utf-8",
        )
        sums = directory / "SHA256SUMS.txt"
        sums.write_text(
            "".join(f"{r['sha256']}  {r['asset']}\n" for r in records),
            encoding="utf-8",
        )
        notes = directory / "release-notes.md"
        notes.write_text(
            "# CrashCourt V1 — preserved research evidence\n\n"
            "These are byte-identical SHA-256-verified copies of formal experiments, "
            "controls, unsuccessful/deviation batches and the mitigation comparison. "
            "The checksummed manifest names their original GitHub artifact IDs and runs.\n\n"
            f"- N8 continuation: https://github.com/{repo}/actions/runs/{study_run} "
            f"(workflow conclusion: **{study_result}**).\n"
            f"- Canonical overhead measurement: https://github.com/{repo}/actions/runs/{bench_run} "
            f"(workflow conclusion: **{bench_result}**).\n\n"
            "A failed workflow may record an original pre-registered prediction mismatch; "
            "the archive does not reclassify its outcome. Do not cite performance numbers "
            "unless the benchmark record independently marks them valid.\n\n"
            "Only a pinned LangGraph/Postgres configuration and deterministic adversarial "
            "schedules were measured. No production incident frequencies or violation "
            "of disclaimed concurrency guarantees are claimed.\n",
            encoding="utf-8",
        )

        args = [
            "gh",
            "release",
            "create",
            TAG,
            "--repo",
            repo,
            "--title",
            "CrashCourt V1 — verified reliability evidence",
            "--notes-file",
            str(notes),
            "--target",
            "main",
            *[str(directory / str(r["asset"])) for r in records],
            str(sums),
            str(manifest),
        ]
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        if result.returncode:
            raise ArchiveError("GitHub release publication failed; inspect GitHub before retrying")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as output:
                output.write(f"Evidence release: https://github.com/{repo}/releases/tag/{TAG}\n")
        print(f"Published {len(records)} verified, scanned artifacts at {TAG}")


def main() -> int:
    repo = os.environ.get("GH_REPO", "")
    run = os.environ.get("BENCH_RUN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        print("Invalid repository identity")
        return 1
    if not run.isdecimal():
        print("Invalid benchmark run id")
        return 1
    try:
        publish(repo, int(run))
    except ArchiveError as exc:
        print(f"Evidence publication blocked: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
