"""Reproduce the source audit's upstream checks (T-03, sections 9 and "scope and method").

Run with network access:

    cd research/evidence/t-03
    uv run python search_upstream.py > upstream-search.txt

It shallow-clones langchain-ai/langgraph at the four pinned tags into a temporary directory, then:
1. compares each audited source file at its tag (repository bytes, `git show`) with the file the
   installed wheel provides;
2. searches the saver, runtime, and conformance tests for the card's pattern and lists every hit
   in the saver tests, a per-file count and the matching test names in the runtime tests, and the
   conformance suite's test counts;
3. lists the repository's branches and the names that match lock, lease, fencing, ownership, or
   writer terms.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = "https://github.com/langchain-ai/langgraph.git"
TAGS = {
    "langgraph": "1.2.12",
    "checkpoint": "checkpoint==4.2.0",
    "postgres": "checkpointpostgres==3.1.2",
    "sqlite": "checkpointsqlite==3.1.1",
}
PATTERN = "concurrent|race|fork|lock|advisory|interprocess"
BRANCH_TERMS = re.compile(r"lock|lease|fenc|owner|writer|concurren|mutex|serializ", re.IGNORECASE)

#: (tag key, path in the repository, installed module) for every langgraph file the audit cites.
AUDITED = [
    (
        "postgres",
        "libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py",
        "langgraph.checkpoint.postgres",
    ),
    (
        "postgres",
        "libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py",
        "langgraph.checkpoint.postgres.aio",
    ),
    (
        "postgres",
        "libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py",
        "langgraph.checkpoint.postgres.base",
    ),
    (
        "postgres",
        "libs/checkpoint-postgres/langgraph/checkpoint/postgres/_internal.py",
        "langgraph.checkpoint.postgres._internal",
    ),
    (
        "sqlite",
        "libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py",
        "langgraph.checkpoint.sqlite",
    ),
    (
        "sqlite",
        "libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/aio.py",
        "langgraph.checkpoint.sqlite.aio",
    ),
    (
        "checkpoint",
        "libs/checkpoint/langgraph/checkpoint/base/__init__.py",
        "langgraph.checkpoint.base",
    ),
    (
        "checkpoint",
        "libs/checkpoint/langgraph/checkpoint/base/id.py",
        "langgraph.checkpoint.base.id",
    ),
    ("langgraph", "libs/langgraph/langgraph/pregel/_loop.py", "langgraph.pregel._loop"),
    ("langgraph", "libs/langgraph/langgraph/pregel/main.py", "langgraph.pregel.main"),
    ("langgraph", "libs/langgraph/langgraph/pregel/_runner.py", "langgraph.pregel._runner"),
    ("langgraph", "libs/langgraph/langgraph/pregel/_executor.py", "langgraph.pregel._executor"),
    ("langgraph", "libs/langgraph/langgraph/pregel/_checkpoint.py", "langgraph.pregel._checkpoint"),
]


def git(clone: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(clone), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return done.stdout


def git_bytes(clone: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True).stdout


def clone(root: Path, key: str) -> Path:
    target = root / key
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            "--branch",
            TAGS[key],
            "--filter=blob:none",
            "--no-checkout",
            REPO,
            str(target),
        ],
        check=True,
    )
    return target


def grep(clone: Path, path: str) -> list[str]:
    done = subprocess.run(
        ["git", "-C", str(clone), "grep", "-n", "-I", "-i", "-E", PATTERN, "HEAD", "--", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.removeprefix("HEAD:") for line in done.stdout.splitlines()]


def test_counts(clone: Path, glob: str) -> tuple[int, int]:
    done = subprocess.run(
        [
            "git",
            "-C",
            str(clone),
            "grep",
            "-c",
            "-E",
            r"^\s*(async )?def test_",
            "HEAD",
            "--",
            glob,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    counts = [int(line.rsplit(":", 1)[1]) for line in done.stdout.splitlines()]
    return sum(counts), len(counts)


CONFORMANCE = "0.0.2"


def sdist_test_counts(root: Path) -> tuple[int, int]:
    """Count capability tests in the released conformance sdist, as for the tag."""
    index = f"https://pypi.org/pypi/langgraph-checkpoint-conformance/{CONFORMANCE}/json"
    meta = json.loads(urllib.request.urlopen(index, timeout=30).read())
    [url] = [u["url"] for u in meta["urls"] if u["packagetype"] == "sdist"]
    archive = root / "conformance.tar.gz"
    archive.write_bytes(urllib.request.urlopen(url, timeout=60).read())
    tests = files = 0
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if "/conformance/spec/test_" in member.name and member.name.endswith(".py"):
                extracted = tar.extractfile(member)
                assert extracted is not None
                found = len(TEST_DEF.findall(extracted.read()))
                tests, files = tests + found, files + (found > 0)
    return tests, files


TEST_DEF = re.compile(rb"^\s*(?:async )?def test_", re.MULTILINE)


def main() -> None:
    run_at = f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}"
    print(f"# Upstream checks for research/source-audit.md, run {run_at}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clones = {key: clone(root, key) for key in TAGS}
        print("\n## Tags")
        for key, path in clones.items():
            print(f"{TAGS[key]:<28} {git(path, 'rev-parse', 'HEAD').strip()}")

        print("\n## Audited files: repository bytes at the tag vs the installed wheel")
        identical = 0
        for key, path, module in AUDITED:
            spec = importlib.util.find_spec(module)
            assert spec is not None and spec.origin is not None, module
            same = git_bytes(clones[key], "show", f"HEAD:{path}") == Path(spec.origin).read_bytes()
            identical += same
            print(f"{'identical' if same else 'DIFFERENT':<10} {TAGS[key]:<28} {path}")
        print(f"{identical} of {len(AUDITED)} identical")

        print(f"\n## Test search: git grep -n -I -i -E '{PATTERN}'")
        for key, path in (
            ("postgres", "libs/checkpoint-postgres/tests"),
            ("sqlite", "libs/checkpoint-sqlite/tests"),
        ):
            hits = grep(clones[key], path)
            print(f"\n### {path} at {TAGS[key]}: {len(hits)} hits")
            print("\n".join(hits))
        runtime = grep(clones["langgraph"], "libs/langgraph/tests")
        print(f"\n### libs/langgraph/tests at {TAGS['langgraph']}: {len(runtime)} hits; per file:")
        per_file: dict[str, int] = {}
        for line in runtime:
            per_file[line.split(":", 1)[0]] = per_file.get(line.split(":", 1)[0], 0) + 1
        for name, count in sorted(per_file.items()):
            print(f"{count:>4}  {name}")
        print("\nTest functions whose names match:")
        name_match = re.compile(rf"^\S+:\d+:\s*(async )?def test_\w*({PATTERN})", re.IGNORECASE)
        print("\n".join(line for line in runtime if name_match.match(line)))
        conformance = grep(clones["langgraph"], "libs/checkpoint-conformance")
        print(f"\n### libs/checkpoint-conformance at {TAGS['langgraph']}: {len(conformance)} hits")
        print("\n".join(conformance))
        tests, files = test_counts(
            clones["langgraph"],
            "libs/checkpoint-conformance/langgraph/checkpoint/conformance/spec/test_*.py",
        )
        print(f"\nConformance capability tests at {TAGS['langgraph']}: {tests} in {files} files")
        released, released_files = sdist_test_counts(root)
        print(
            f"Conformance capability tests in the {CONFORMANCE} sdist on PyPI: "
            f"{released} in {released_files} files"
        )

    print("\n## Branches: git ls-remote --heads")
    heads = subprocess.run(
        ["git", "ls-remote", "--heads", REPO], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    names = sorted(line.split("refs/heads/", 1)[1] for line in heads)
    print(f"{len(names)} branches; names matching {BRANCH_TERMS.pattern}:")
    print("\n".join(name for name in names if BRANCH_TERMS.search(name)) or "(none)")
    print("\nAll branch names:")
    print("\n".join(names))


if __name__ == "__main__":
    sys.exit(main())
