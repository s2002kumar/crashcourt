"""What git can prove about order: which commit last changed a file, whether one commit is an
ancestor of another, and what a file held at a commit.

The pre-registration and the checker freeze are commitments in git order, not promises: the freeze
commit is an ancestor of the pre-registration commit, which is an ancestor of every batch it covers,
and the pre-registration did not change between its commit and the batch's. These helpers read
that order; they never write to the repository.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path, PurePosixPath


class GitError(RuntimeError):
    """git could not answer, or the path is not in a repository."""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False, timeout=60
    )
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.decode(errors='replace').strip()}")
    return result


def head(repo: Path) -> str:
    """The full sha of the checked-out commit."""
    return _git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def resolve(repo: Path, ref: str) -> str | None:
    """The full sha a ref names, or None when it names no commit here."""
    result = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return result.stdout.decode().strip() if result.returncode == 0 else None


def dirty(repo: Path) -> list[str]:
    """Every changed, staged, or untracked path git reports; empty means a clean tree."""
    out = _git(repo, "status", "--porcelain").stdout.decode()
    return [line for line in out.splitlines() if line.strip()]


def last_commit(repo: Path, path: PurePosixPath | str, at: str = "HEAD") -> str | None:
    """The newest commit at or before `at` that changed `path`, or None if none did."""
    out = _git(repo, "log", "-1", "--format=%H", at, "--", str(path)).stdout.decode().strip()
    return out or None


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    """True when `older` is `newer` or one of its ancestors."""
    result = _git(repo, "merge-base", "--is-ancestor", older, newer, check=False)
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise GitError(f"merge-base {older} {newer}: {result.stderr.decode(errors='replace').strip()}")


def blob(repo: Path, commit: str, path: PurePosixPath | str) -> bytes | None:
    """The file's bytes as committed at `commit`, or None when it does not exist there."""
    result = _git(repo, "show", f"{commit}:{path}", check=False)
    return result.stdout if result.returncode == 0 else None


def blob_sha256(repo: Path, commit: str, path: PurePosixPath | str) -> str | None:
    data = blob(repo, commit, path)
    return None if data is None else hashlib.sha256(data).hexdigest()
