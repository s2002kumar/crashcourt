"""`crashcourt experiment`: check the pre-registration, authorize a batch, verify batches, and
summarize them. Heavy imports wait until a subcommand runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="experiment_command", required=True)
    check = sub.add_parser(
        "check", help="validate research/preregistration.md; list what is missing"
    )
    check.add_argument("--root", type=Path, default=Path())
    check.add_argument(
        "--write",
        action="store_true",
        help="rewrite the JSON block canonically and the cell table from it",
    )
    authorize = sub.add_parser(
        "authorize", help="run the gate for a pre-registered scenario; print the authorization"
    )
    authorize.add_argument("scenario", type=Path)
    authorize.add_argument("--trials", type=int, required=True)
    authorize.add_argument("--root", type=Path, default=Path())
    authorize.add_argument(
        "--instrument", type=Path, nargs="*", default=[], help="instrument batch directories"
    )
    verify = sub.add_parser("verify", help="re-prove archived batches against git")
    verify.add_argument("batches", type=Path, nargs="+")
    verify.add_argument("--root", type=Path, default=Path())
    aggregate = sub.add_parser("aggregate", help="cells.json from verified batches")
    aggregate.add_argument("batches", type=Path, nargs="+")
    aggregate.add_argument("--root", type=Path, default=Path())
    aggregate.add_argument("--out", type=Path, default=Path("cells.json"))
    aggregate.add_argument(
        "--rules", type=Path, help="recorded compatibility rules (a JSON list); none by default"
    )


def _check(args: argparse.Namespace) -> int:
    from crashcourt.experiment.preregistration import (
        PREREGISTRATION_PATH,
        canonical,
        parse,
    )

    path = args.root / PREREGISTRATION_PATH
    try:
        text = path.read_text(encoding="utf-8")
        prereg = parse(text)
        tidy = canonical(text)
    except (OSError, ValueError) as exc:  # PreregistrationError is a ValueError
        print(f"{path}: {exc}")
        return 1
    if args.write:
        path.write_bytes(tidy.encode("utf-8"))
    elif tidy != text:
        print(f"{path}: the cell table or the JSON block is not canonical; run with --write")
        return 1
    phases = {p: sum(c.phase == p for c in prereg.cells) for p in ("instrument", "new-evidence")}
    print(
        f"{path}: {prereg.state}, {len(prereg.cells)} cells "
        f"({phases['instrument']} instrument, {phases['new-evidence']} new evidence)"
    )
    if prereg.state == "draft":
        runnable = prereg.runnable()
        print(f"the draft can authorize: {', '.join(runnable) or 'no cell'}")
    missing = prereg.missing()
    if missing:
        print(f"before it can be final ({len(missing)}):")
        print("\n".join(f"  {m}" for m in missing))
    return 0


def _authorize(args: argparse.Namespace) -> int:
    from crashcourt.experiment.aggregate import authorize
    from crashcourt.experiment.gate import NotAuthorized

    try:
        auth = authorize(args.scenario, args.trials, args.root, args.instrument)
    except NotAuthorized as exc:
        print(f"not authorized ({len(exc.reasons)}):")
        print("\n".join(f"  {r}" for r in exc.reasons))
        return 1
    print(auth.to_json(), end="")
    return 0


def _verify(args: argparse.Namespace) -> int:
    from crashcourt.experiment.verify import BatchNotVerified, verify_batch

    failed = 0
    for batch in args.batches:
        try:
            verified = verify_batch(batch, args.root)
        except BatchNotVerified as exc:
            failed += 1
            print(f"{batch}: not verified ({len(exc.problems)}):")
            print("\n".join(f"  {p}" for p in exc.problems))
            continue
        a = verified.authorization
        print(
            f"{batch}: verified: cell {a.cell}, {len(verified.trials)} trials, pre-registration "
            f"{a.preregistration_commit[:12]}, checker {a.checker_sha256[:12]}"
        )
    return 1 if failed else 0


def _aggregate(args: argparse.Namespace) -> int:
    from crashcourt.experiment.aggregate import AggregationRefused, aggregate
    from crashcourt.experiment.fingerprint import CompatibilityRule

    rules: list[CompatibilityRule] = []
    if args.rules is not None:
        raw = json.loads(args.rules.read_text(encoding="utf-8"))
        rules = [CompatibilityRule.model_validate(r) for r in raw]
    try:
        report = aggregate(args.batches, args.root, rules)
    except AggregationRefused as exc:
        print(f"aggregation refused ({len(exc.problems)}):")
        print("\n".join(f"  {p}" for p in exc.problems))
        return 1
    args.out.write_bytes(report.to_json().encode("utf-8"))
    for cell in report.cells:
        statuses = ", ".join(g.status for g in cell.groups)
        print(f"{cell.cell}: {statuses}{'' if cell.pooled else ' (not pooled)'}")
    print(f"wrote {args.out}")
    return 0


def run(args: argparse.Namespace) -> int:
    handlers = {
        "check": _check,
        "authorize": _authorize,
        "verify": _verify,
        "aggregate": _aggregate,
    }
    return handlers[args.experiment_command](args)
