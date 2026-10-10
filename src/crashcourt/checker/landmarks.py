"""Landmarks: where in a run a failpoint arm is meant to fire, as data, and their extraction.

A landmark names a saver call by its context (red-team specification 4.2): the call's own fields
(`data.source`, `data.channels`, its task) and the events around it in the same op (after a
node's `node.end`, before another node's `node.start`). The checker holds only these generic
rules; which rules make up each landmark of 4.2 is a table in scenarios/landmarks/, and no
landmark and no occurrence number is written anywhere in code.

Occurrence numbers are derived, never guessed: `locate` finds, in real fault-free histories, the
one call per (worker, op) each landmark names, and `occurrences` checks that every trial puts it
at the same occurrence. A table stays `draft` until that derivation has been audited; hits are
verified against a draft table only in synthetic trials and artifact mutants.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from crashcourt.checker.artifacts import ContextRule, LandmarkSpec, LandmarkTable
from crashcourt.checker.facts import data_list, node_name, pointer
from crashcourt.spec.events import Event

#: The repository's landmark tables (scenarios/landmarks/*.yaml).
LANDMARKS_ROOT = Path(__file__).resolve().parents[3] / "scenarios" / "landmarks"


def load_table(path: Path) -> LandmarkTable:
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return LandmarkTable.model_validate(data)


def repository_tables(root: Path = LANDMARKS_ROOT) -> list[LandmarkTable]:
    """Every table in the repository; a scenario names one by its file stem, which must be its
    name."""
    paths = sorted(root.glob("*.yaml"), key=lambda p: p.name) if root.is_dir() else []
    tables = [load_table(p) for p in paths]
    for path, table in zip(paths, tables, strict=True):
        if path.stem != table.name:
            raise ValueError(f"{path.name} holds table {table.name}; the names must agree")
    return tables


def rule_holds(
    rule: ContextRule,
    op_id: str | None,
    position: float,
    call: Event | None,
    events: Sequence[Event],
) -> bool:
    """Whether a rule holds for a call whose failpoint sits at `position` in its process.

    `position` is the failpoint's place in the process's seq order: a real hit's seq, or, for a
    fault-free history, just after the call (`.before`) or just before its return (`.after`).
    """
    same_op = [e for e in events if e.op_id == op_id]
    before = [e for e in same_op if e.seq < position]
    match rule.rule:
        case "call_data_equals":
            return (
                call is not None and rule.key is not None and call.data.get(rule.key) == rule.value
            )
        case "call_channels_include":
            return call is not None and rule.channel in data_list(call, "channels")
        case "call_task_of_node":
            starts = [e for e in before if e.kind == "node.start" and node_name(e) == rule.node]
            return call is not None and bool(starts) and call.task_id == starts[-1].task_id
        case "after_node_end":
            return any(e.kind == "node.end" and node_name(e) == rule.node for e in before)
        case "before_node_start":
            return not any(e.kind == "node.start" and node_name(e) == rule.node for e in before)
        case "no_kind_before":
            return not any(e.kind == rule.kind for e in before)
        case "first_call":
            method = call.kind.split(".")[1] if call is not None else ""
            return not any(e.kind == f"saver.{method}.call" and e is not call for e in before)
        case "last_call":
            method = call.kind.split(".")[1] if call is not None else ""
            return not any(e.kind == f"saver.{method}.call" and e.seq > position for e in same_op)


def matches(spec: LandmarkSpec, call: Event, position: float, events: Sequence[Event]) -> bool:
    return call.kind == f"saver.{spec.method}.call" and all(
        rule_holds(rule, call.op_id, position, call, events) for rule in spec.rules
    )


# -- extraction from fault-free histories -------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """Where a landmark sits in one fault-free history."""

    landmark: str
    worker: str
    op_id: str
    #: The nth call of the landmark's method in that worker and op, counting from 1: the
    #: occurrence a failpoint arm on (worker, op) must name.
    occurrence: int
    pointer: str


def _position(call: Event, events: Sequence[Event], point: str) -> float:
    if point == "before":
        return call.seq + 0.5
    method = call.kind.split(".")[1]
    for event in events[call.seq + 1 :]:
        if event.kind in {f"saver.{method}.return", f"saver.{method}.raise"} and (
            event.thread_id,
            event.checkpoint_id,
            event.task_id,
        ) == (call.thread_id, call.checkpoint_id, call.task_id):
            return event.seq - 0.5
    return len(events) + 0.5


def locate(
    table: LandmarkTable, by_proc: Mapping[str, Sequence[Event]], point: str = "before"
) -> tuple[list[Site], list[str]]:
    """Each landmark's sites in one fault-free trial, and the problems: a landmark that no call
    or more than one call of a (worker, op) matches is not a landmark."""
    sites: list[Site] = []
    problems: list[str] = []
    for spec in table.landmarks:
        found: dict[tuple[str, str], list[tuple[int, Event]]] = defaultdict(list)
        for worker, events in sorted(by_proc.items()):
            counts: dict[str, int] = defaultdict(int)
            for call in events:
                if call.kind != f"saver.{spec.method}.call" or call.op_id is None:
                    continue
                counts[call.op_id] += 1
                if matches(spec, call, _position(call, events, point), events):
                    found[(worker, call.op_id)].append((counts[call.op_id], call))
        if not found:
            problems.append(f"{spec.id}: no call matches")
        for (worker, op_id), calls in sorted(found.items()):
            if len(calls) != 1:
                where = ", ".join(pointer(c) for _, c in calls)
                problems.append(f"{spec.id}: {len(calls)} calls match in {worker} {op_id}: {where}")
                continue
            occurrence, call = calls[0]
            sites.append(Site(spec.id, worker, op_id, occurrence, pointer(call)))
    return sites, problems


def occurrences(
    trials: Iterable[Sequence[Site]],
) -> tuple[dict[tuple[str, str, str], int], list[str]]:
    """The occurrence of each (landmark, worker, op) across trials, and the problems: a site that
    moves between trials, or appears in some trials and not others, is not deterministic."""
    seen: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    present: dict[tuple[str, str, str], int] = defaultdict(int)
    count = 0
    for sites in trials:
        count += 1
        for site in sites:
            key = (site.landmark, site.worker, site.op_id)
            seen[key].add(site.occurrence)
            present[key] += 1
    stable: dict[tuple[str, str, str], int] = {}
    problems: list[str] = []
    for key in sorted(seen):
        if len(seen[key]) != 1:
            problems.append(f"{key}: occurrence differs between trials: {sorted(seen[key])}")
        elif present[key] != count:
            problems.append(f"{key}: found in {present[key]} of {count} trials")
        else:
            stable[key] = next(iter(seen[key]))
    return stable, problems
