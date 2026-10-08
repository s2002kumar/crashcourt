from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.cli import main
from crashcourt.manifest import Manifest

from ._factories import GOOD_VERSIONS, WINDOWS_WHEEL, FakeProbes, make_manifest


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


def test_compare_with_an_old_manifest_fails_cleanly(
    probes: FakeProbes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = tmp_path / "old.json"
    old.write_text('{"schema_version": 1, "git_sha": "abc"}', encoding="utf-8")
    args = ["doctor", "--manifest", str(tmp_path / "m.json"), "--compare", str(old)]
    assert main(args) == 2
    assert f"cannot compare with {old}: not a schema 1 manifest: missing" in capsys.readouterr().out


def test_compare_prints_libpq_from_another_platform_wheel(
    probes: FakeProbes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "windows.json"
    windows = make_manifest(
        psycopg_wheel=WINDOWS_WHEEL, libpq_version="18.4", libpq_build_version="18.4"
    )
    other.write_text(windows.to_json(), encoding="utf-8")
    args = ["doctor", "--manifest", str(tmp_path / "m.json"), "--compare", str(other)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "note: libpq 18.6" in out and "18.4" in out
    assert "pinned fields match" in out


def test_compare_with_a_missing_file_fails_cleanly(
    probes: FakeProbes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nowhere.json"
    args = ["doctor", "--manifest", str(tmp_path / "m.json"), "--compare", str(missing)]
    assert main(args) == 2
    assert f"cannot compare with {missing}:" in capsys.readouterr().out


def test_compare_refuses_a_reference_that_fails_validation(
    probes: FakeProbes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other.json"
    other.write_text(make_manifest(psycopg_wheel="").to_json(), encoding="utf-8")
    args = ["doctor", "--manifest", str(tmp_path / "m.json"), "--compare", str(other)]
    assert main(args) == 2
    out = capsys.readouterr().out
    assert "it is not usable as evidence" in out and "psycopg_wheel is missing" in out
