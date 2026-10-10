"""The demonstration scenarios the fixture batches are generated from.

A fixture batch names its scenario by content hash, like a harness batch (`scenario_sha256`,
crashcourt.spec.content), so each demonstration scenario is a frozen, validated model and its hash
is computed, never typed in. These models describe what the generator writes; they are not the
harness's scenario format, which the orchestrator defines. Nothing here is run against a target.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

#: Every identifier the generator writes starts with this, so a fixture can never pass for a run.
DEMO = "DEMO-"

DemoId = Annotated[str, StringConstraints(pattern=r"^DEMO-\S+$")]

#: The scripted trial shapes the generator knows (crashcourt.fixtures.demo).
Story = Literal[
    "fault-free",
    "forced-fork",
    "conflicting-resume",
    "kill-after-effect",
    "stop-continue",
    "lost-ack",
    "database-restart",
    "fan-out-latency",
    "stale-worker",
]


class DemoCell(BaseModel):
    """One cell of a demonstration scenario: a workload, a fault, and how its trials are written."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workload: str
    #: The fault from the spec's taxonomy, or None for a fault-free control.
    fault: str | None
    durability: Literal["sync", "async"]
    #: Deterministic cells use trigger-only schedules; randomized cells fire faults at a seeded
    #: time offset.
    kind: Literal["deterministic", "randomized"]
    #: The control this cell stands in for (C1, C2), or None for an ordinary cell.
    control: str | None = None
    trials: Annotated[int, Field(ge=1, le=10)]
    story: Story


class DemoScenario(BaseModel):
    """A demonstration scenario. Its content hash is the fixture batch's `scenario_sha256`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: DemoId
    note: str = "Demonstration scenario for crashcourt fixtures --demo. Never run against a target."
    cells: tuple[DemoCell, ...] = Field(min_length=1)


#: The matrix batch: both controls the overview and methodology show, deterministic cells for
#: every fault mark the explorer draws, and one randomized cell.
MATRIX = DemoScenario(
    name="DEMO-scenario-matrix",
    cells=(
        DemoCell(
            workload="W1",
            fault=None,
            durability="sync",
            kind="deterministic",
            control="C1",
            trials=2,
            story="fault-free",
        ),
        DemoCell(
            workload="W5",
            fault="F06",
            durability="sync",
            kind="deterministic",
            control="C2",
            trials=2,
            story="forced-fork",
        ),
        DemoCell(
            workload="W1",
            fault="F08",
            durability="sync",
            kind="deterministic",
            trials=3,
            story="conflicting-resume",
        ),
        DemoCell(
            workload="W1",
            fault="F01",
            durability="sync",
            kind="deterministic",
            trials=2,
            story="kill-after-effect",
        ),
        DemoCell(
            workload="W1",
            fault="F01",
            durability="async",
            kind="deterministic",
            trials=2,
            story="kill-after-effect",
        ),
        DemoCell(
            workload="W1",
            fault="F03",
            durability="sync",
            kind="deterministic",
            trials=2,
            story="stop-continue",
        ),
        DemoCell(
            workload="W1",
            fault="F11",
            durability="sync",
            kind="deterministic",
            trials=2,
            story="lost-ack",
        ),
        DemoCell(
            workload="W1",
            fault="F13",
            durability="sync",
            kind="deterministic",
            trials=2,
            story="database-restart",
        ),
        DemoCell(
            workload="W2",
            fault="F12",
            durability="sync",
            kind="randomized",
            trials=4,
            story="fan-out-latency",
        ),
    ),
)

#: The compare scenario: one cell, run by two batches against two demo runtimes. It names no
#: target, so both batches carry the same scenario hash.
COMPARE = DemoScenario(
    name="DEMO-scenario-compare",
    cells=(
        DemoCell(
            workload="W1",
            fault="F05",
            durability="sync",
            kind="deterministic",
            trials=2,
            story="stale-worker",
        ),
    ),
)
