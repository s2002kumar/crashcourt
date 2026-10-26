"""The documented shell commands run as written when copied. Every fenced shell block in the README
and docs/ parses as bash, and no line ends in an escaped backslash: inside a fenced block it copies
literally, so bash reads a backslash argument and ends the command instead of continuing it, which
broke the release download in docs/REPRODUCING.md."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]
FENCE = re.compile(r"^(```|~~~)(?:bash|sh|shell)\n(.*?)^\1[ \t]*$", re.MULTILINE | re.DOTALL)
#: `runs/<batch>` and the like: a value the reader fills in, not shell syntax.
PLACEHOLDER = re.compile(r"<[A-Za-z][A-Za-z0-9_-]*>")


def shell_blocks() -> Iterator[Any]:
    for doc in DOCS:
        for n, match in enumerate(FENCE.finditer(doc.read_text(encoding="utf-8")), start=1):
            yield pytest.param(match.group(2), id=f"{doc.relative_to(REPO).as_posix()}#{n}")


BLOCKS = list(shell_blocks())


def test_the_docs_hold_the_shell_blocks_this_checks() -> None:
    names = {block.id.split("#")[0] for block in BLOCKS}
    assert {"README.md", "docs/REPRODUCING.md"} <= names


@pytest.mark.parametrize("block", BLOCKS)
def test_no_command_line_ends_in_an_escaped_backslash(block: str) -> None:
    escaped = [line for line in block.splitlines() if line.rstrip().endswith("\\\\")]
    assert not escaped, escaped


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason="bash -n on Linux; CI runs it",
)
@pytest.mark.parametrize("block", BLOCKS)
def test_every_shell_block_parses(block: str) -> None:
    done = subprocess.run(
        ["bash", "-n"],
        input=PLACEHOLDER.sub("value", block),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
