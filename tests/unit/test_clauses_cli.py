from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.clauses.snapshot import snapshot_sha256
from crashcourt.cli import main

from .test_clauses import clause_data, write

# The clauses cite https://docs.example.com/page#section, so the page defines that id.
PAGE = b'# Page\n\n<a id="section" />\n\nSomething the docs say, exactly.\n'


@pytest.fixture
def clauses_dir(tmp_path: Path) -> Path:
    root = tmp_path / "clauses"
    write(root, clause_data(snapshot_sha256=snapshot_sha256(PAGE)))
    write(root, clause_data(id="LG-OTHER", polarity="permits", title="Pipe | in title"))
    return root


def test_check_accepts_a_valid_registry(
    clauses_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["clauses", "check", "--dir", str(clauses_dir)]) == 0
    assert "2 clauses valid" in capsys.readouterr().out


def test_check_rejects_an_invalid_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, clause_data(polarity="promises"))
    assert main(["clauses", "check", "--dir", str(tmp_path)]) == 1
    assert "LG-TEST.yaml: polarity" in capsys.readouterr().out


def test_refetch_reports_drift_without_failing(
    clauses_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("crashcourt.clauses.snapshot.fetch", lambda url: PAGE)
    cache = tmp_path / "cache"
    args = ["clauses", "check", "--dir", str(clauses_dir), "--refetch", "--cache-dir", str(cache)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "ok     LG-TEST" in out
    assert "DRIFT  LG-OTHER" in out
    assert "1 unchanged, 1 drifted, 0 unreachable" in out
    assert len(list(cache.iterdir())) == 1


def test_list_markdown_writes_an_lf_file(clauses_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "registry.md"
    args = ["clauses", "list", "--dir", str(clauses_dir), "--markdown", "--output", str(out)]
    assert main(args) == 0
    raw = out.read_bytes()
    assert b"\r" not in raw
    text = raw.decode()
    assert "| LG-OTHER | permits | Pipe \\| in title |" in text
    assert text.index("LG-OTHER") < text.index("LG-TEST")


def test_list_prints_one_line_per_clause(
    clauses_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["clauses", "list", "--dir", str(clauses_dir)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [line.split()[0] for line in lines] == ["LG-OTHER", "LG-TEST"]
