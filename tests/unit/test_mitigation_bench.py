"""The overhead benchmark's arithmetic and report, on synthetic records: nearest-rank percentiles,
the seeded schedule, the per-round summary, and the overheads against base."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from crashcourt.mitigation.bench import (
    ARMS,
    SINK_KINDS,
    BenchOptions,
    describe,
    percentile,
    report,
    schedule,
    summarize_round,
)


def test_percentile_is_the_nearest_rank() -> None:
    samples = list(range(1, 101))  # 1..100
    assert percentile(samples, 0.5) == 50
    assert percentile(samples, 0.99) == 99
    assert percentile(samples, 1.0) == 100
    assert percentile([7], 0.99) == 7
    assert percentile([3, 1, 2], 0.5) == 2  # unsorted input
    with pytest.raises(ValueError, match="no samples"):
        percentile([], 0.5)
    with pytest.raises(ValueError, match="q must be"):
        percentile([1], 0)


def test_describe_counts_every_sample() -> None:
    assert describe([4, 1, 3, 2]) == {
        "n": 4,
        "p50_ns": 2,
        "p99_ns": 4,
        "mean_ns": 2,
        "min_ns": 1,
        "max_ns": 4,
    }


def test_the_schedule_holds_each_kind_equally_and_depends_only_on_the_seed() -> None:
    first = schedule(ARMS, 5, random.Random(1))
    assert sorted(first) == sorted([arm for arm in ARMS for _ in range(5)])
    assert first == schedule(ARMS, 5, random.Random(1))
    assert first != schedule(ARMS, 5, random.Random(2))


def test_options_refuse_empty_phases() -> None:
    assert BenchOptions(out=Path("x")).problems() == []
    assert BenchOptions(out=Path("x"), units=0, warmup=-1).problems() == [
        "units must be at least 1",
        "warmup must not be negative",
    ]


def _unit(ms: float, calls: dict[str, list[int]] | None = None) -> dict[str, Any]:
    ns = int(ms * 1e6)
    return {
        "thread": "t",
        "start_ns": ns // 2,
        "resume_ns": ns - ns // 2,
        "unit_ns": ns,
        "late": 0,
        "calls": calls or {"aput": [100_000, 200_000]},
    }


def _round() -> dict[str, Any]:
    cost = {"base": 10.0, "F": 11.0, "I": 10.5, "FI": 12.0}
    latency = {
        arm: {"warmup": [_unit(99.0)], "units": [_unit(cost[arm] + i / 10) for i in range(10)]}
        for arm in ARMS
    }
    rate = {"base": 100.0, "F": 90.0, "I": 95.0, "FI": 80.0}
    blocks = {
        arm: {"wall_ns": 10**9, "units_per_s": rate[arm], "units": [_unit(cost[arm])] * 4}
        for arm in ARMS
    }
    kinds = {kind: {"warmup": [5_000], "posts": [1_000, 2_000, 3_000]} for kind in SINK_KINDS}
    return {
        "latency": {"order": [], "arms": latency},
        "throughput": {"order": list(ARMS), "blocks": blocks},
        "ledger": {"order": [], "kinds": kinds, "ledgers": {}},
    }


def test_a_round_summary_leaves_out_the_warm_up_and_compares_each_arm_with_base() -> None:
    summary = summarize_round(_round())
    base, fenced = summary["latency"]["base"], summary["latency"]["F"]
    assert base["unit"]["n"] == 10  # the warm-up unit of 99 ms is not counted
    assert base["unit"]["max_ns"] == int(10.9 * 1e6)
    assert "overhead" not in base
    assert fenced["overhead"]["p50_ratio"] == pytest.approx(
        fenced["unit"]["p50_ns"] / base["unit"]["p50_ns"] - 1
    )
    assert fenced["overhead"]["p50_delta_ns"] == fenced["unit"]["p50_ns"] - base["unit"]["p50_ns"]
    assert fenced["calls"]["aput"]["n"] == 20 and fenced["calls_per_unit"]["aput"] == 2
    assert summary["throughput"]["FI"]["rate_ratio"] == pytest.approx(-0.2)
    assert "rate_ratio" not in summary["throughput"]["base"]
    assert summary["ledger"]["once-duplicate"]["n"] == 3  # its warm-up append is not counted


def test_the_report_names_the_runner_and_every_arm_of_every_round() -> None:
    rounds = [_round(), _round()]
    record = {
        "options": {
            "rounds": 2,
            "units": 10,
            "warmup": 1,
            "concurrency": 2,
            "per_client": 2,
            "sink_posts": 3,
            "seed": 1,
        },
        "host": {
            "runner": "github-hosted",
            "cpu_model": "Some CPU",
            "cpu_count": 4,
            "memory_gib": 16,
            "platform": "Linux",
            "image": "ubuntu24 20261023.334.3",
        },
        "manifest": {"docker_version": "28.0.4", "images": {"postgres": "postgres:18.6"}},
        "git": {"sha": "abc", "dirty": False},
        "started": "s",
        "finished": "f",
        "valid": True,
        "problems": [],
        "summary": [summarize_round(r) for r in rounds],
    }
    text = report(record)
    assert "## Round 1" in text and "## Round 2" in text
    assert "github-hosted; Some CPU; 4 CPUs; 16 GiB" in text
    assert "ubuntu24 20261023.334.3" in text
    assert "checks: all passed" in text
    assert "no interval is computed" in text
    for arm in ARMS:
        assert f"| {arm} | 10 |" in text
    # Base's units take 10.0-10.9 ms and F's 11.0-11.9 ms: p50 10.40 and 11.40, p99 10.90 and
    # 11.90, so F is +9.6% at p50 and +9.2% at p99; its block ran 90 units/s to base's 100.
    assert "| F | 10 | 11.40 | 11.90 | 11.45 | +9.6% | +9.2% | 90.0 | -10.0% |" in text
    assert "| base | 10 | 10.40 | 10.90 | 10.45 | - | - | 100.0 | - |" in text
