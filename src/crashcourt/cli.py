"""Crashcourt command line. `doctor` verifies the pinned environment and writes the manifest."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from crashcourt import __version__
from crashcourt.doctor import format_report, run_doctor
from crashcourt.manifest import Manifest, diff_pinned


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
        other = Manifest.from_json(args.compare.read_text(encoding="utf-8"))
        diffs = diff_pinned(report.manifest, other)
        if diffs:
            print("pinned fields differ from", args.compare)
            print("\n".join(f"  {d}" for d in diffs))
            return 2
        print("pinned fields match", args.compare)
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
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args)
    parser.error(f"unknown command {args.command}")
    return 2
