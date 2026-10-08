"""Which code is serving the ledger: a hash of the crashcourt package's Python source.

The sink reports it in /health and doctor compares it with the checkout's, so a sink built from
another commit, or an image compose reused, cannot pass for the one the manifest names.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import crashcourt


def source_sha256(package: Path | None = None) -> str:
    """sha256 over every .py file under the package, by POSIX relative path and content."""
    root = package or Path(crashcourt.__file__).resolve().parent
    files = sorted(
        (path.relative_to(root).as_posix(), path)
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    digest = hashlib.sha256()
    for name, path in files:
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
