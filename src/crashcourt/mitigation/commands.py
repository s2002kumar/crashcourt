"""`crashcourt mitigation`: the mitigation contract over finished batches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="mitigation_command", required=True)
    contract = sub.add_parser(
        "contract",
        help="evaluate the mitigation contract on each trial of a batch; write contract.json "
        "per trial and mitigation.json per batch",
    )
    contract.add_argument("batch", type=Path, nargs="+", help="batch directories")
    contract.add_argument("--root", type=Path, default=Path(), help="the repository root")
    contract.add_argument(
        "--dry-run", action="store_true", help="print the summary and write nothing"
    )
    bench = sub.add_parser(
        "bench",
        help="measure what the fence and the idempotent sink cost fault-free W1 runs: "
        "interleaved arms, p50, p99, throughput, every raw duration (not an experiment)",
    )
    bench.add_argument("--out", type=Path, required=True, help="a new directory for the results")
    bench.add_argument("--root", type=Path, default=Path(), help="the repository root")
    bench.add_argument("--rounds", type=int, default=3)
    bench.add_argument("--units", type=int, default=300, help="measured units per arm per round")
    bench.add_argument("--warmup", type=int, default=30, help="warm-up units per arm per round")
    bench.add_argument("--concurrency", type=int, default=8, help="clients per throughput block")
    bench.add_argument("--per-client", type=int, default=25, help="units per throughput client")
    bench.add_argument("--sink-posts", type=int, default=300, help="sink posts per kind per round")
    bench.add_argument("--seed", type=int, default=1, help="the arms' order depends only on this")
    bench.add_argument(
        "--i-own-this-database",
        action="store_true",
        help="allow a target database that is not on this host",
    )


def _line(summary: dict[str, Any]) -> str:
    def share(item: dict[str, Any]) -> str:
        interval = item.get("wilson95")
        bounds = f" [{interval[0]:.2f}, {interval[1]:.2f}]" if interval else ""
        return f"{item['count']}/{item['of']}{bounds}"

    stale = summary["stale"]
    return (
        f"{summary['cell']}: {summary['readable']}/{summary['trials']} readable; "
        f"fork {share(summary['fork'])}; acknowledged is head "
        f"{share(summary['acknowledged_is_head'])}; stale: none durable "
        f"{share(stale['none_durable'])}, every attempt fenced "
        f"{share(stale['every_attempt_fenced'])}; logical {summary['logical']}; "
        f"attempt consistency {summary['effects']['attempt_consistency']}"
    )


def _contract(args: argparse.Namespace) -> int:
    from crashcourt.mitigation.contract import (
        NotWritten,
        evaluate_batch,
        summarize,
        write_records,
    )

    status = 0
    for batch in args.batch:
        try:
            if args.dry_run:
                summary = summarize(batch, evaluate_batch(batch))
            else:
                _, summary = write_records(batch, args.root)
        except NotWritten as exc:
            print(f"not written: {exc}")
            status = 1
            continue
        except (OSError, ValueError, KeyError) as exc:
            print(f"{batch}: cannot evaluate: {type(exc).__name__}: {exc}")
            status = 1
            continue
        print(_line(summary))
        for unreadable in summary["unreadable"]:
            print(f"  trial {unreadable['trial']}: {'; '.join(unreadable['problems'])}")
        judgement = summary.get("judgement")
        if judgement is not None:
            print(
                f"  contract: {judgement['status']} ({judgement['trials_as_predicted']} of "
                f"{judgement['readable']} readable trials as predicted)"
            )
            for name, measure in judgement["measures"].items():
                if measure["mismatches"]:
                    print(
                        f"    {name}: predicted {measure['predicted']!r}, "
                        f"found {measure['mismatches']}"
                    )
            if judgement["status"] != "as-predicted":
                status = 1
        if summary["v1"]:
            print(f"  v1 beside: {json.dumps(summary['v1'], sort_keys=True)}")
    return status


def _bench(args: argparse.Namespace) -> int:
    from crashcourt.mitigation.bench import BenchError, BenchOptions, run_bench

    options = BenchOptions(
        out=args.out,
        rounds=args.rounds,
        units=args.units,
        warmup=args.warmup,
        concurrency=args.concurrency,
        per_client=args.per_client,
        sink_posts=args.sink_posts,
        seed=args.seed,
    )
    try:
        record = run_bench(options, args.root, own_database=args.i_own_this_database)
    except BenchError as exc:
        print(f"bench: {exc}")
        return 1
    print((args.out / "bench.md").read_text(encoding="utf-8"))
    if not record["valid"]:
        print(f"bench: the checks failed, so this measurement does not stand: {record['problems']}")
        return 1
    return 0


def run(args: argparse.Namespace) -> int:
    return {"contract": _contract, "bench": _bench}[args.mitigation_command](args)
