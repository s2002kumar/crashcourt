"""Pin compose images by digest (used by scripts/pin_images.py). The rewrite logic is pure and tested."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

IMAGE_LINE = re.compile(
    r"^(?P<lead>\s*image:\s*)(?P<ref>[^\s#@]+)(?:@sha256:[0-9a-f]{64})?(?P<rest>\s*(?:#.*)?)$"
)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def repo_of(ref: str) -> str:
    """`postgres:18` -> `postgres`; `host:5000/a/b:1` -> `host:5000/a/b`."""
    last = ref.rsplit("/", 1)[-1]
    return ref.rsplit(":", 1)[0] if ":" in last else ref


def normalize_repo(name: str) -> str:
    for prefix in ("docker.io/", "index.docker.io/"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return name.removeprefix("library/")


def rewrite_compose(text: str, resolve: Callable[[str], str]) -> tuple[str, list[str]]:
    """Return compose text with every `image:` pinned to the digest `resolve(ref)` returns."""
    out: list[str] = []
    changes: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n")
        match = IMAGE_LINE.match(body)
        if not match:
            out.append(line)
            continue
        ref = match.group("ref")
        digest = resolve(ref)
        if not DIGEST.fullmatch(digest):
            raise ValueError(f"resolver returned a malformed digest for {ref}: {digest!r}")
        new = f"{match.group('lead')}{ref}@{digest}{match.group('rest')}"
        if new != body:
            changes.append(f"{ref} -> {digest}")
        out.append(new + ("\n" if line.endswith("\n") else ""))
    return "".join(out), changes


def docker_digest(ref: str) -> str:
    """Pull `ref` and return the registry digest recorded for its repository."""
    subprocess.run(["docker", "pull", ref], check=True)
    raw = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    wanted = normalize_repo(repo_of(ref))
    entries: list[str] = json.loads(raw) or []
    for entry in entries:
        name, _, digest = entry.partition("@")
        if normalize_repo(name) == wanted and DIGEST.fullmatch(digest):
            return digest
    raise RuntimeError(f"no registry digest for {ref} in {entries}")


def main(compose_path: Path) -> int:
    text = compose_path.read_text(encoding="utf-8")
    new_text, changes = rewrite_compose(text, docker_digest)
    if new_text != text:
        compose_path.write_text(new_text, encoding="utf-8")
    for change in changes or ["already pinned; digests unchanged"]:
        print(change)
    return 0
