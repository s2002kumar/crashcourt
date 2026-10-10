"""Shared fixtures for the unit tests."""

from __future__ import annotations

import pytest

from crashcourt.checker import engine, status


@pytest.fixture
def pre_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    """The checker as it ran before its freeze: the pre-freeze guard on, no FROZEN.md check.

    Every other test runs the frozen checker, which verifies itself and the trial's landmark table
    against FROZEN.md before classifying. Tests of logic the freeze does not change, on landmark
    tables that are not the frozen ones, and tests of the pre-freeze guard itself, use this."""
    monkeypatch.setattr(status, "FREEZE_STATE", "PRE-FREEZE")
    monkeypatch.setattr(engine, "FREEZE_STATE", "PRE-FREEZE")
