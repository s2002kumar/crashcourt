from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.faults.base import Data, FaultError, inject, inject_instant
from crashcourt.history.reader import read_history
from crashcourt.history.writer import HistoryWriter, RunIdentity

RUN = RunIdentity(batch="DEMO-faults", cell="DEMO-cell", trial=0, seed="s")


def history(tmp_path: Path) -> HistoryWriter:
    return HistoryWriter(tmp_path / "orchestrator.jsonl", run=RUN, proc="orchestrator")


def kinds(tmp_path: Path) -> list[tuple[str, str | None, dict[str, object]]]:
    events = read_history(tmp_path / "orchestrator.jsonl").events
    return [(e.kind, e.fault_id, dict(e.data)) for e in events]


def heal_status(tmp_path: Path) -> tuple[str | None, str | None]:
    heal = read_history(tmp_path / "orchestrator.jsonl").events[-1]
    assert heal.kind == "fault.heal"
    return heal.status, heal.error


def test_inject_is_written_before_the_fault_applies(tmp_path: Path) -> None:
    order: list[str] = []
    with history(tmp_path) as h:

        def apply() -> Data:
            order.append(read_history(tmp_path / "orchestrator.jsonl").events[-1].kind)
            return {"applied": True}

        fault = inject(h, "f-1", {"fault": "x"}, apply, lambda: {"healed": True})
        assert fault.observed == {"applied": True}
        fault.heal()
        fault.heal()  # a second heal is a no-op
    assert order == ["fault.inject"]
    # The heal carries what applying observed and what healing returned.
    assert kinds(tmp_path) == [
        ("fault.inject", "f-1", {"fault": "x"}),
        ("fault.heal", "f-1", {"applied": True, "healed": True}),
    ]
    assert heal_status(tmp_path) == ("ok", None)


def test_failed_apply_is_closed_with_a_heal_and_raises(tmp_path: Path) -> None:
    def apply() -> None:
        raise OSError("proxy down")

    with history(tmp_path) as h, pytest.raises(FaultError, match="proxy down"):
        inject(h, "f-2", {}, apply)
    assert kinds(tmp_path) == [("fault.inject", "f-2", {}), ("fault.heal", "f-2", {})]
    assert heal_status(tmp_path) == ("fail", "OSError: proxy down")


def test_failed_heal_is_recorded_and_raises(tmp_path: Path) -> None:
    def heal() -> None:
        raise OSError("cannot remove")

    with history(tmp_path) as h:
        fault = inject(h, "f-3", {}, lambda: {"toxic": "t"}, heal)
        with pytest.raises(FaultError, match="cannot remove"):
            fault.heal()
        fault.heal()  # already closed out; nothing more is written
    assert kinds(tmp_path)[-1] == ("fault.heal", "f-3", {"toxic": "t"})
    assert heal_status(tmp_path) == ("fail", "OSError: cannot remove")
    assert len(kinds(tmp_path)) == 2


def test_context_manager_heals_on_exit(tmp_path: Path) -> None:
    with history(tmp_path) as h, inject(h, "f-4", {}, lambda: None) as fault:
        assert not fault.healed
    assert fault.healed
    assert [k for k, _, _ in kinds(tmp_path)] == ["fault.inject", "fault.heal"]


def test_instant_fault_heals_at_once_with_what_it_observed(tmp_path: Path) -> None:
    with history(tmp_path) as h:
        observed = inject_instant(h, "f-5", {"fault": "kill"}, lambda: {"returncode": -9})
    assert observed == {"returncode": -9}
    assert kinds(tmp_path) == [
        ("fault.inject", "f-5", {"fault": "kill"}),
        ("fault.heal", "f-5", {"returncode": -9}),
    ]
    assert heal_status(tmp_path) == ("ok", None)


def test_a_failed_fault_keeps_what_it_observed(tmp_path: Path) -> None:
    def apply() -> None:
        raise FaultError("one backend outlived the wait", {"matched": 3, "terminated": 2})

    with history(tmp_path) as h, pytest.raises(FaultError) as raised:
        inject(h, "f-6", {}, apply)
    assert raised.value.observed == {"matched": 3, "terminated": 2}
    assert kinds(tmp_path)[-1] == ("fault.heal", "f-6", {"matched": 3, "terminated": 2})
    assert heal_status(tmp_path) == ("fail", "FaultError: one backend outlived the wait")
