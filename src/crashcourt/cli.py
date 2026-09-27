"""Crashcourt command line.

`doctor` verifies the pinned environment and writes the manifest; `clauses` checks and lists the
clause registry.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from crashcourt import __version__
from crashcourt.clauses import ClauseRegistry, RegistryError
from crashcourt.clauses import report as clause_report
from crashcourt.clauses import snapshot as clause_snapshot
from crashcourt.doctor import format_report, run_doctor
from crashcourt.manifest import Manifest, diff_pinned, platform_notes, validate_manifest


def _doctor(args: argparse.Namespace) -> int:
    from crashcourt.probes import SystemProbes  # lazy: keeps `--help` free of side effects

    report = run_doctor(SystemProbes(Path.cwd()))
    if args.json:
        payload = {
            "ok": report.ok,
            "checks": [check.__dict__ for check in report.checks],
            "manifest": report.manifest.to_dict() if report.manifest else None,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    if report.manifest is None:
        return 1
    # Bytes, not text mode: the manifest must be byte-identical on every platform.
    args.manifest.write_bytes(report.manifest.to_json().encode("utf-8"))
    if args.compare is not None:
        try:
            other = Manifest.from_json(args.compare.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # ValueError covers ManifestFormatError and bad JSON
            print(f"cannot compare with {args.compare}: {exc}")
            return 2
        problems = validate_manifest(other)
        if problems:
            print(f"cannot compare with {args.compare}: it is not usable as evidence")
            print("\n".join(f"  {p}" for p in problems))
            return 2
        for note in platform_notes(report.manifest, other):
            print("note:", note)
        diffs = diff_pinned(report.manifest, other)
        if diffs:
            print("pinned fields differ from", args.compare)
            print("\n".join(f"  {d}" for d in diffs))
            return 2
        print("pinned fields match", args.compare)
    return 0


def _load_clauses(root: Path) -> ClauseRegistry | None:
    try:
        return ClauseRegistry.load(root)
    except RegistryError as exc:
        print(f"clauses: {len(exc.problems)} problem(s) in {root}")
        print("\n".join(f"  {p}" for p in exc.problems))
        return None


def _clauses_check(args: argparse.Namespace) -> int:
    registry = _load_clauses(args.dir)
    if registry is None:
        return 1
    print(f"clauses: {len(registry)} clauses valid in {args.dir}")
    if args.refetch:
        # Pass the fetcher explicitly so it is looked up at call time.
        reports = clause_snapshot.refetch(registry, args.cache_dir, clause_snapshot.fetch)
        print(clause_report.drift_text(reports), end="")
    return 0


def _clauses_list(args: argparse.Namespace) -> int:
    registry = _load_clauses(args.dir)
    if registry is None:
        return 1
    if args.markdown:
        text = clause_report.listing_markdown(registry)
    else:
        text = clause_report.listing_text(registry)
    if args.output is None:
        print(text, end="")
    else:
        args.output.write_bytes(text.encode("utf-8"))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crashcourt", description=__doc__)
    parser.add_argument("--version", action="version", version=f"crashcourt {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser(
        "doctor", help="verify the pinned environment; write manifest.json"
    )
    doctor.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    doctor.add_argument(
        "--compare", type=Path, help="fail if pinned fields differ from this manifest"
    )
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")

    clauses = commands.add_parser("clauses", help="check or list the clause registry")
    clause_commands = clauses.add_subparsers(dest="clauses_command", required=True)
    check = clause_commands.add_parser("check", help="validate every clause file (offline)")
    check.add_argument("--dir", type=Path, default=Path("clauses"))
    check.add_argument(
        "--refetch",
        action="store_true",
        help="also re-download each source and report hash or quote drift (never fails)",
    )
    check.add_argument("--cache-dir", type=Path, default=Path(".cache/clauses"))
    listing = clause_commands.add_parser("list", help="list clauses: id, polarity, source, date")
    listing.add_argument("--dir", type=Path, default=Path("clauses"))
    listing.add_argument("--markdown", action="store_true", help="print a Markdown table")
    listing.add_argument("--output", type=Path, help="write to this file instead of stdout")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "clauses":
        return _clauses_check(args) if args.clauses_command == "check" else _clauses_list(args)
    parser.error(f"unknown command {args.command}")
    return 2
