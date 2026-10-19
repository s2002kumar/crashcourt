"""Crashcourt command line.

`doctor` verifies the pinned environment and writes the manifest; `clauses` checks and lists the
clause registry; `schema export` writes the v1 JSON Schemas; `faults selftest` checks every fault
primitive against the compose services; `run` runs a batch of trials of one scenario (Linux only);
`experiment` checks the pre-registration, authorizes a pre-registered batch, re-proves archived
batches, and summarizes them into cells.json; `fixtures --demo` writes the explorer's labeled demo
artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
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


def _schema_export(args: argparse.Namespace) -> int:
    from crashcourt.spec import export  # lazy: keeps `--help` fast

    if args.check:
        problems = export.differences(args.out)
        if problems:
            print(f"schemas in {args.out} are out of date; run `crashcourt schema export`")
            print("\n".join(f"  {p}" for p in problems))
            return 1
        print(f"schemas in {args.out} match the models")
        return 0
    for path in export.export(args.out):
        print(f"wrote {path}")
    return 0


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is not at least 1")
    return value


def _faults_selftest(args: argparse.Namespace) -> int:
    from crashcourt.faults import selftest
    from crashcourt.probes import DEFAULT_PG_DSN, DEFAULT_PG_PROXY_DSN, DEFAULT_TOXIPROXY_URL

    def progress(trial: selftest.Trial) -> None:
        marks = " ".join(f"{c.name}={c.status}" for c in trial.checks)
        print(f"trial {trial.number:>2}: {'ok  ' if trial.ok else 'FAIL'}  {marks}", flush=True)

    results = selftest.run_selftest(
        trials=args.trials,
        direct_dsn=os.environ.get("CRASHCOURT_PG_DSN", DEFAULT_PG_DSN),
        proxied_dsn=os.environ.get("CRASHCOURT_PG_PROXY_DSN", DEFAULT_PG_PROXY_DSN),
        toxiproxy_url=os.environ.get("CRASHCOURT_TOXIPROXY_URL", DEFAULT_TOXIPROXY_URL),
        root=Path.cwd(),
        history_dir=args.history,
        restart=not args.no_restart,
        progress=progress,
    )
    text = selftest.report(results)
    print(text, end="")
    if args.report is not None:
        args.report.write_bytes(text.encode("utf-8"))
    return 0 if all(t.ok for t in results) else 1


def _run(args: argparse.Namespace) -> int:
    from crashcourt.faults.base import FaultError
    from crashcourt.orchestrator.runner import (
        RunOptions,
        RunRefused,
        Settings,
        TrialResult,
        require_linux,
        run_batch,
    )
    from crashcourt.orchestrator.topology import TopologyError
    from crashcourt.spec.scenario import ScenarioError, load_scenario

    def progress(trial: TrialResult) -> None:
        counts = " ".join(f"{k}={v}" for k, v in sorted(trial.outcomes.items()))
        state = "ok     " if trial.valid else "INVALID"
        detail = (trial.reasons or trial.observations or [""])[0]
        print(f"trial {trial.number:>4}: {state}  {counts}  {detail}".rstrip(), flush=True)

    try:
        require_linux()
        scenario = load_scenario(args.scenario)
        settings = Settings.from_environment(Path.cwd())
        options = RunOptions(
            trials=args.trials,
            out=args.out,
            batch=args.batch,
            canonical=args.canonical,
            own_database=args.i_own_this_database,
            instrument=tuple(args.instrument),
        )
        summary = run_batch(
            scenario,
            options,
            settings,
            source=args.scenario,
            progress=progress,
            warn=lambda text: print(f"warning: {text}", flush=True),
        )
    except (RunRefused, ScenarioError, TopologyError, FaultError) as exc:
        print(f"run refused: {exc}")
        return 2
    if summary.revoked:
        print("authorization revoked: " + "; ".join(summary.revoked), flush=True)
    valid = sum(t.valid for t in summary.trials)
    print(
        f"batch {summary.batch}: {len(summary.trials)} trials, {valid} valid; "
        f"written to {summary.directory}"
    )
    return 0 if summary.valid else 1


def _experiment(args: argparse.Namespace) -> int:
    from crashcourt.experiment import commands  # lazy: keeps `--help` fast

    return commands.run(args)


def _mitigation(args: argparse.Namespace) -> int:
    from crashcourt.mitigation import commands  # lazy: keeps `--help` fast

    return commands.run(args)


def _fixtures(args: argparse.Namespace) -> int:
    from crashcourt import fixtures  # lazy: keeps `--help` fast

    files = fixtures.generate(fixtures.DEFAULT_SEED if args.seed is None else args.seed)
    if args.check:
        problems = fixtures.differences(args.out, files)
        if problems:
            print(f"fixtures in {args.out} are out of date; run `crashcourt fixtures --demo`")
            print("\n".join(f"  {p}" for p in problems))
            return 1
        print(f"fixtures in {args.out} match the generator ({len(files)} files)")
        return 0
    try:
        fixtures.write(args.out, files)
    except fixtures.FixtureDirError as exc:
        print(f"fixtures: {exc}")
        return 1
    print(f"wrote {len(files)} demo files to {args.out}")
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
    schema = commands.add_parser("schema", help="JSON Schemas of the v1 models")
    schema_commands = schema.add_subparsers(dest="schema_command", required=True)
    export = schema_commands.add_parser("export", help="write schemas/v1/*.json")
    export.add_argument("--out", type=Path, default=Path("schemas/v1"))
    export.add_argument(
        "--check", action="store_true", help="fail if the files differ from the models"
    )

    faults = commands.add_parser("faults", help="fault primitives")
    fault_commands = faults.add_subparsers(dest="faults_command", required=True)
    selftest = fault_commands.add_parser(
        "selftest", help="check every fault primitive against the compose services"
    )
    selftest.add_argument("--trials", type=_positive_int, default=20)
    selftest.add_argument(
        "--no-restart", action="store_true", help="skip the database restart check"
    )
    selftest.add_argument("--report", type=Path, help="also write the Markdown report here")
    selftest.add_argument("--history", type=Path, help="keep the fault histories in this directory")
    experiment = commands.add_parser(
        "experiment", help="the pre-registration, its run gate, verification, and cell summaries"
    )
    from crashcourt.experiment.commands import add_arguments

    add_arguments(experiment)
    mitigation = commands.add_parser(
        "mitigation", help="the mitigation contract over finished batches (the N8 study)"
    )
    from crashcourt.mitigation.commands import add_arguments as add_mitigation_arguments

    add_mitigation_arguments(mitigation)
    fixture = commands.add_parser(
        "fixtures", help="write the explorer's demo artifacts (DEMO- ids, provenance fixture)"
    )
    fixture.add_argument(
        "--demo", action="store_true", required=True, help="demo data; never a result"
    )
    fixture.add_argument("--out", type=Path, default=Path("site/fixtures"))
    fixture.add_argument("--seed", type=int, help="the output depends only on this (default 1)")
    fixture.add_argument(
        "--check", action="store_true", help="fail if the files differ from the generator's"
    )

    run = commands.add_parser(
        "run", help="run trials of a scenario: two workers, barriers, histories (Linux only)"
    )
    run.add_argument("scenario", type=Path, help="the scenario YAML file")
    run.add_argument("--trials", type=_positive_int, default=1)
    run.add_argument("--out", type=Path, default=Path("runs"), help="batches go here")
    run.add_argument("--batch", help="the batch id (default: b- and the UTC time)")
    run.add_argument(
        "--canonical",
        action="store_true",
        help="a publishable batch: refuse a dirty checkout or a sink clock off by over 1 s",
    )
    run.add_argument(
        "--instrument",
        type=Path,
        nargs="+",
        action="extend",
        default=[],
        metavar="BATCH",
        help="instrument batch directories a pre-registered new-evidence cell rests on",
    )
    run.add_argument(
        "--i-own-this-database",
        action="store_true",
        help="allow a target database that is not on this host",
    )

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "clauses":
        return _clauses_check(args) if args.clauses_command == "check" else _clauses_list(args)
    if args.command == "schema":
        return _schema_export(args)
    if args.command == "faults":
        return _faults_selftest(args)
    if args.command == "run":
        return _run(args)
    if args.command == "experiment":
        return _experiment(args)
    if args.command == "mitigation":
        return _mitigation(args)
    if args.command == "fixtures":
        return _fixtures(args)
    parser.error(f"unknown command {args.command}")
    return 2
