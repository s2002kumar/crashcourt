"""Which code is serving the ledger: a hash of what the sink image is built from.

The hash covers the crashcourt package's Python source and the files that decide the rest of the
image: pyproject.toml, uv.lock, .python-version, and Dockerfile.harness. The sink reports it in
/health and doctor compares it with the checkout's, so a sink built from another checkout, or an
image compose reused, cannot pass for the one the manifest names.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import crashcourt

#: The build inputs beside the package, relative to the build root.
BUILD_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "Dockerfile.harness",
)


def build_root() -> Path:
    """Where the build inputs are: $CRASHCOURT_SOURCE_ROOT (the image sets /app), else here."""
    return Path(os.environ.get("CRASHCOURT_SOURCE_ROOT", "."))


def source_sha256(root: Path | None = None, package: Path | None = None) -> str:
    """sha256 over the build files under `root` and every .py file under `package`, each by its
    POSIX relative path and content. A missing build file is hashed as missing, not skipped."""
    root = build_root() if root is None else root
    package = package or Path(crashcourt.__file__).resolve().parent
    digest = hashlib.sha256()
    for name in BUILD_FILES:
        path = root / name
        content = hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"missing"
        digest.update(f"build/{name}".encode() + b"\0" + content)
    files = sorted(
        (path.relative_to(package).as_posix(), path)
        for path in package.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    for name, path in files:
        digest.update(
            f"package/{name}".encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest()
        )
    return digest.hexdigest()
