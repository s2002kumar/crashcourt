from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.cli import main
from crashcourt.manifest import Manifest

from ._factories import GOOD_VERSIONS, FakeProbes, make_manifest


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> FakeProbes:
    fake = FakeProbes()
    monkeypatch.setattr("crashcourt.probes.SystemProbes", lambda root: fake)
    return fake


def test_doctor_writes_manifest_with_lf_line_endings(probes: FakeProbes, tmp_path: Path) -> None:
    out = tmp_path / "manifest.json"
    assert main(["doctor", "--manifest", str(out)]) == 0
    raw = out.read_bytes()
    assert b"\r" not in raw
    assert Manifest.from_json(raw.decode()).to_json().encode() == raw


def test_doctor_failure_writes_nothing(probes: FakeProbes, tmp_path: Path) -> None:
    probes.docker = None
    out = tmp_path / "manifest.json"
    assert main(["doctor", "--manifest", str(out)]) == 1
    assert not out.exists()


def test_compare_matches_identical_pins(probes: FakeProbes, tmp_path: Path) -> None:
    other = tmp_path / "other.json"
    other.write_text(make_manifest(machine="arm64").to_json(), encoding="utf-8")
    args = ["doctor", "--manifest", str(tmp_path / "m.json"), "--compare", str(other)]
    assert main(args) == 0


def test_compare_fails_on_pinned_drift(
    probes: FakeProbes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other.json"
    drifted = make_manifest(packages={**GOOD_VERSIONS, "psycopg": "9.9.9"})
    other.write_text(drifted.to_json(), encoding="utf-8")
    args = ["doctor", "--manifest", str(tmp_path / "m.json"), "--compare", str(other)]
    assert main(args) == 2
    assert "packages:" in capsys.readouterr().out


def test_compare_fails_on_python_patch_drift(
    probes: FakeProbes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other.json"
    other.write_text(make_manifest(python_version="3.12.3").to_json(), encoding="utf-8")
    args = ["doctor", "--manifest", str(tmp_path / "m.json"), "--compare", str(other)]
    assert main(args) == 2
    assert "python_version: '3.12.7' != '3.12.3'" in capsys.readouterr().out
