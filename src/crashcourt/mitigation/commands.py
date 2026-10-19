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


def run(args: argparse.Namespace) -> int:
    return {"contract": _contract}[args.mitigation_command](args)
