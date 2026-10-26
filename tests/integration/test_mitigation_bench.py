"""The overhead benchmark end to end, at its smallest, against the compose services: every arm
runs real W1 units through the worker's `execute`, the fenced arms claim and bind their epochs,
every check holds, the files are written, and the twin databases are gone afterwards."""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from crashcourt.mitigation.bench import (
    ARMS,
    DATABASES,
    SINK_KINDS,
    BenchError,
    BenchOptions,
    prepared_databases,
    run_bench,
)
from crashcourt.probes import DEFAULT_PG_DSN

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def test_a_small_benchmark_measures_every_arm_and_cleans_up(tmp_path: Path) -> None:
    options = BenchOptions(
        out=tmp_path / "bench",
        rounds=2,
        units=3,
        warmup=1,
        concurrency=2,
        per_client=2,
        sink_posts=2,
    )
    record = run_bench(options, ROOT, progress=lambda _: None)
    assert record["valid"], record["problems"]
    assert json.loads((options.out / "bench.json").read_text(encoding="utf-8"))["valid"]
    assert "## Round 2" in (options.out / "bench.md").read_text(encoding="utf-8")
    for summary in record["summary"]:
        for arm in ARMS:
            latency = summary["latency"][arm]
            assert latency["unit"]["n"] == 3
            assert summary["throughput"][arm]["units"] == 4
            # A fenced unit claims its thread twice (start and resume); an unfenced one never.
            assert latency["calls_per_unit"].get("claim", 0) == (2 if arm in {"F", "FI"} else 0)
            assert latency["calls_per_unit"]["ledger.append"] == 1
        for kind in SINK_KINDS:
            assert summary["ledger"][kind]["n"] == 2
    dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
    with psycopg.connect(dsn, autocommit=True) as conn:
        left = conn.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)", (list(DATABASES.values()),)
        ).fetchall()
    assert left == []


def test_an_existing_output_directory_is_refused(tmp_path: Path) -> None:
    from crashcourt.mitigation.bench import BenchError

    (tmp_path / "bench").mkdir()
    with pytest.raises(BenchError, match="never overwrites"):
        run_bench(BenchOptions(out=tmp_path / "bench"), ROOT, progress=lambda _: None)


def test_preexisting_second_database_is_preserved_and_partial_setup_is_cleaned() -> None:
    """If the second name is in use, undo the first CREATE and preserve the existing database."""
    dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
    with psycopg.connect(dsn, autocommit=True) as conn:
        existing = conn.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
            (list(DATABASES.values()),),
        ).fetchall()
        if existing:
            pytest.skip("benchmark database name in use; do not touch its owner")
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASES["fenced"])))
    try:
        with pytest.raises(BenchError, match="already exists"), prepared_databases(dsn):
            pytest.fail("must refuse to enter a benchmark with an existing database")
        with psycopg.connect(dsn, autocommit=True) as conn:
            found = {
                name
                for (name,) in conn.execute(
                    "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
                    (list(DATABASES.values()),),
                ).fetchall()
            }
        assert found == {DATABASES["fenced"]}
    finally:
        # Only the fenced database was created by this test; never touch any other database.
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(DATABASES["fenced"]))
            )


def test_fence_install_failure_cleans_up_both_created_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even failure after saver setup leaves no benchmark databases behind."""
    from crashcourt.mitigation import fence

    dsn = os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN)
    with psycopg.connect(dsn, autocommit=True) as conn:
        existing = conn.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
            (list(DATABASES.values()),),
        ).fetchall()
    if existing:
        pytest.skip("benchmark database name in use; do not touch its owner")

    def fail_install(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic fence install failure")

    monkeypatch.setattr(fence, "install", fail_install)
    with (
        pytest.raises(RuntimeError, match="synthetic fence install failure"),
        prepared_databases(dsn),
    ):
        pytest.fail("fence install must fail before yielding")
    with psycopg.connect(dsn, autocommit=True) as conn:
        found = conn.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
            (list(DATABASES.values()),),
        ).fetchall()
    assert found == []
