"""Offline guards for the V1 evidence-release archiver.

These tests do not contact GitHub or publish a release. Only the archive scanner and
checksum arithmetic and benchmark success gating are exercised offline.
Download provenance is checked again by the real run.
"""

from __future__ import annotations

import hashlib
import runpy
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS: dict[str, Any] = runpy.run_path(str(ROOT / "scripts" / "publish_evidence.py"))
ArchiveError = TOOLS["ArchiveError"]
scan_contents = TOOLS["scan_contents"]
digest_file = TOOLS["digest_file"]


def make_archive(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)


def test_release_scanner_accepts_a_valid_evidence_archive(tmp_path: Path) -> None:
    path = tmp_path / "valid.zip"
    make_archive(path, {"study.json": '{"status":"stopped"}', "trial/ledger.jsonl": '{"n":2}'})
    scan_contents(path)
    assert digest_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_scanner_refuses_path_traversal(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.zip"
    make_archive(path, {"../outside.txt": "not safe"})
    with pytest.raises(ArchiveError, match="Unsafe archive entry"):
        scan_contents(path)


def test_release_scanner_refuses_unintended_secrets_without_revealing_them(
    tmp_path: Path,
) -> None:
    path = tmp_path / "token.zip"
    dummy = "gh" + "p_" + ("X" * 30)
    make_archive(path, {"session.json": '{"test":"' + dummy + '"}'})
    with pytest.raises(ArchiveError, match="Github credential") as failure:
        scan_contents(path)
    assert dummy not in str(failure.value)


def test_release_scanner_refuses_private_key_file_name(tmp_path: Path) -> None:
    path = tmp_path / "private.zip"
    make_archive(path, {".env": "EXAMPLE=placeholder"})
    with pytest.raises(ArchiveError, match="Potential private file"):
        scan_contents(path)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped", None])
def test_publication_refuses_unsuccessful_benchmark_before_accessing_artifacts(
    monkeypatch: pytest.MonkeyPatch, conclusion: str | None
) -> None:
    namespace = TOOLS["publish"].__globals__
    monkeypatch.setitem(namespace, "release_exists", lambda _repo: False)
    monkeypatch.setitem(
        namespace,
        "gh",
        lambda _repo, _endpoint: {
            "name": "bench",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": conclusion,
        },
    )

    def no_artifacts(_repo: str) -> None:
        raise AssertionError("a failed benchmark must never lead to artifact acquisition")

    monkeypatch.setitem(namespace, "find_continuation", no_artifacts)
    with pytest.raises(ArchiveError, match="did not succeed"):
        TOOLS["publish"]("example/crashcourt", 456)


def test_successful_benchmark_reaches_normal_study_provenance_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = TOOLS["publish"].__globals__
    monkeypatch.setitem(namespace, "release_exists", lambda _repo: False)
    monkeypatch.setitem(
        namespace,
        "gh",
        lambda _repo, _endpoint: {
            "name": "bench",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
        },
    )

    def check_study(_repo: str) -> None:
        raise RuntimeError("study provenance reached")

    monkeypatch.setitem(namespace, "find_continuation", check_study)
    with pytest.raises(RuntimeError, match="study provenance reached"):
        TOOLS["publish"]("example/crashcourt", 456)
