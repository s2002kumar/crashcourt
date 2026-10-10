// The artifact tree as the pages read it: batches, their trials, and the ids the dynamic routes
// export. Every file is validated against schemas/v1, and every rule that spans files is checked here,
// so a page renders only artifacts that agree with each other:
// - each top-level directory is a batch: batch.json and manifest.json, and the batch's
//   manifest_sha256 is the sha256 of that manifest.json's bytes;
// - a directory holding result.json is a trial, and its histories are the .jsonl files beside it;
// - a result names its batch, the batch's manifest hash, and the batch's provenance, and every event
//   in a trial names the result's batch, cell, and trial;
// - no two batches share an id, and no two trials share a cell and trial number, so each has one page.
// Nothing here decides an outcome: results are shown as recorded.

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { artifactRoot, isRelease, walk } from "./artifacts.ts";
import { combined, type Provenance, type Sourced } from "./provenance.ts";
import {
  ArtifactError,
  parseBatch,
  parseEvents,
  parseManifest,
  parseResult,
  type LoadedEvent,
} from "./schema/validate.ts";
import type { BatchRecord, InvariantResult, Manifest, ResultRecord } from "./schema/types.gen.ts";

export interface History {
  /** Path relative to the artifact root, with forward slashes. */
  readonly file: string;
  readonly events: readonly LoadedEvent[];
}

export interface Trial extends Sourced {
  readonly batch: string;
  readonly cell: string;
  readonly trial: number;
  /** The trial's directory, relative to the artifact root. */
  readonly dir: string;
  readonly result: ResultRecord;
  /** Every history in the trial's directory, by file name: one per process, and the merge. */
  readonly histories: readonly History[];
  /** merged.jsonl, the histories in causal order, when the trial has one. */
  readonly merged: History | null;
}

export interface Batch extends Sourced {
  readonly id: string;
  readonly dir: string;
  readonly record: BatchRecord;
  readonly manifest: Manifest;
  readonly trials: readonly Trial[];
}

export interface Batches {
  readonly root: string | null;
  readonly batches: readonly Batch[];
  readonly trials: readonly Trial[];
}

const posix = (p: string): string => p.split(path.sep).join("/");
const parent = (p: string): string => path.posix.dirname(p);

function fail(problems: ArtifactError[], file: string, problem: string): void {
  problems.push(new ArtifactError(file, undefined, [problem]));
}

/** Reads and cross-checks every artifact under `root`; throws with every problem found. */
export function loadBatches(root: string): Batches {
  if (!existsSync(root)) {
    throw new AggregateError([], `${root}: the artifact directory does not exist`);
  }
  const problems: ArtifactError[] = [];
  const read = <T>(rel: string, parse: (text: string, file: string) => T): T | null => {
    try {
      return parse(readFileSync(path.join(root, rel), "utf8"), rel);
    } catch (e) {
      if (!(e instanceof ArtifactError)) throw e;
      problems.push(e);
      return null;
    }
  };

  const files = [...walk(root)].map((f) => posix(path.relative(root, f))).sort();
  const byDir = new Map<string, string[]>();
  for (const file of files) {
    if (!file.includes("/")) {
      fail(problems, file, "a file outside any batch directory");
      continue;
    }
    const top = file.slice(0, file.indexOf("/"));
    byDir.set(top, [...(byDir.get(top) ?? []), file]);
  }

  const batches: Batch[] = [];
  for (const [dir, inside] of byDir) {
    const batchFile = `${dir}/batch.json`;
    const manifestFile = `${dir}/manifest.json`;
    if (!inside.includes(batchFile) || !inside.includes(manifestFile)) {
      fail(problems, dir, "a batch directory holds batch.json and manifest.json");
      continue;
    }
    const record = read(batchFile, parseBatch);
    const manifest = read(manifestFile, parseManifest);
    if (record === null || manifest === null) continue;
    const digest = createHash("sha256")
      .update(readFileSync(path.join(root, manifestFile)))
      .digest("hex");
    if (digest !== record.manifest_sha256) {
      fail(problems, batchFile, `manifest_sha256 is not the sha256 of ${manifestFile} (${digest})`);
    }

    const trialDirs = new Set(inside.filter((f) => f.endsWith("/result.json")).map(parent));
    const trials: Trial[] = [];
    for (const file of inside) {
      if (file === batchFile || file === manifestFile) continue;
      const name = path.posix.basename(file);
      if (name !== "result.json" && !name.endsWith(".jsonl")) {
        fail(problems, file, "only batch.json and manifest.json sit beside a batch's trials");
      } else if (!trialDirs.has(parent(file))) {
        fail(problems, file, "a history outside any trial directory (one holding result.json)");
      }
    }
    for (const trialDir of [...trialDirs].sort()) {
      const result = read(`${trialDir}/result.json`, parseResult);
      if (result === null) continue;
      const where = `${trialDir}/result.json`;
      if (result.batch !== record.batch) {
        fail(problems, where, `names batch ${result.batch}, not ${record.batch}`);
      }
      if (result.manifest_sha256 !== record.manifest_sha256) {
        fail(problems, where, "manifest_sha256 differs from its batch's");
      }
      if (result.provenance !== record.provenance) {
        fail(problems, where, `provenance ${result.provenance} in a ${record.provenance} batch`);
      }
      const histories: History[] = [];
      for (const file of inside.filter((f) => parent(f) === trialDir && f.endsWith(".jsonl"))) {
        const events = read(file, parseEvents);
        if (events === null) continue;
        const stranger = events.find(
          (e) => e.batch !== result.batch || e.cell !== result.cell || e.trial !== result.trial,
        );
        if (stranger) {
          fail(problems, file, `an event of ${stranger.batch} ${stranger.cell} #${stranger.trial}`);
        }
        histories.push({ file, events });
      }
      trials.push({
        batch: result.batch,
        cell: result.cell,
        trial: result.trial,
        dir: trialDir,
        provenance: result.provenance,
        result,
        histories,
        merged: histories.find((h) => h.file.endsWith("/merged.jsonl")) ?? null,
      });
    }
    batches.push({
      id: record.batch,
      dir,
      provenance: record.provenance,
      record,
      manifest,
      trials,
    });
  }

  const ids = new Map<string, string>();
  for (const b of batches) {
    const other = ids.get(b.id);
    if (other) fail(problems, b.dir, `batch ${b.id} is also in ${other}`);
    ids.set(b.id, b.dir);
  }
  const routes = new Map<string, string>();
  const trials = batches.flatMap((b) => b.trials);
  for (const t of trials) {
    const route = `/trials/${t.cell}/${t.trial}/`;
    const other = routes.get(route);
    if (other) fail(problems, t.dir, `${route} would show both this trial and ${other}`);
    routes.set(route, t.dir);
  }

  if (problems.length > 0) {
    throw new AggregateError(problems, problems.map((p) => p.message).join("\n"));
  }
  const order = (a: Trial, b: Trial) =>
    a.batch.localeCompare(b.batch) || a.cell.localeCompare(b.cell) || a.trial - b.trial;
  return {
    root,
    batches: batches.sort((a, b) => a.id.localeCompare(b.id)),
    trials: trials.sort(order),
  };
}

const NO_BATCHES: Batches = { root: null, batches: [], trials: [] };
let cached: Batches | undefined;

/**
 * The build's artifacts, read once per process. A release build fails here, before any page renders,
 * if ../results holds no batch or anything that is not a run.
 */
export function evidence(): Batches {
  if (cached) return cached;
  const root = artifactRoot();
  const loaded = root === null ? NO_BATCHES : loadBatches(root);
  if (isRelease()) {
    if (loaded.batches.length === 0) throw new Error(`release: no batch in ${root}`);
    const fixture = [...loaded.batches, ...loaded.trials].find((s) => s.provenance !== "run");
    if (fixture) throw new Error(`release: ${JSON.stringify(fixture.dir)} is not a harness run`);
  }
  cached = loaded;
  return cached;
}

// -- what the pages look up ------------------------------------------------------------------

/** The batch whose manifest was generated last (ties by id): what the overview calls the latest run. */
export function latestBatch(all: Batches): Batch | null {
  const sorted = [...all.batches].sort(
    (a, b) =>
      b.manifest.generated_at.localeCompare(a.manifest.generated_at) || a.id.localeCompare(b.id),
  );
  return sorted[0] ?? null;
}

export function batchOf(all: Batches, trial: Trial): Batch {
  const found = all.batches.find((b) => b.id === trial.batch);
  if (!found) throw new Error(`no batch ${trial.batch}`);
  return found;
}

export function findTrial(all: Batches, cell: string, trial: string): Trial | undefined {
  return all.trials.find((t) => t.cell === cell && String(t.trial) === trial);
}

/** The lineage route's trial segment: the cell and the trial number, `<cell>.<trial>`. */
export function trialKey(t: Trial): string {
  return `${t.cell}.${t.trial}`;
}

export function findTrialByKey(all: Batches, key: string): Trial | undefined {
  return all.trials.find((t) => trialKey(t) === key);
}

/** The threads a trial's events touch, in the order they first appear. */
export function threadsOf(t: Trial): string[] {
  const events = t.merged?.events ?? t.histories.flatMap((h) => h.events);
  return [...new Set(events.flatMap((e) => (e.thread_id ? [e.thread_id] : [])))];
}

export interface Occurrence {
  readonly trial: Trial;
  readonly result: InvariantResult;
}

export interface Finding extends Sourced {
  /** The route id: the last path segment of the repro reference. */
  readonly id: string;
  readonly repro: string;
  readonly occurrences: readonly Occurrence[];
}

/**
 * Findings until the harness writes finding records (T-14): the outcomes that carry a repro, grouped by
 * it. The schema allows a repro only on an anomaly, so this picks nothing by outcome.
 */
export function findings(all: Batches): Finding[] {
  const groups = new Map<string, Occurrence[]>();
  for (const trial of all.trials) {
    for (const result of trial.result.results) {
      if (result.repro)
        groups.set(result.repro, [...(groups.get(result.repro) ?? []), { trial, result }]);
    }
  }
  const found = [...groups].map(([repro, occurrences]) => ({
    id: repro.split("/").at(-1) ?? repro,
    repro,
    occurrences,
    provenance: combined(occurrences.map((o) => o.trial)),
  }));
  const ids = new Map<string, string>();
  for (const f of found) {
    const other = ids.get(f.id);
    if (other) throw new Error(`repros ${other} and ${f.repro} would share /findings/${f.id}/`);
    ids.set(f.id, f.repro);
  }
  return found.sort((a, b) => a.id.localeCompare(b.id));
}

export interface Comparison extends Sourced {
  /** The scenario's content hash, which every batch in the comparison ran. */
  readonly scenario: string;
  readonly batches: readonly Batch[];
}

/** A batch's runtime as its manifest pins it: the package names, which differ between runtimes. */
export function runtimeOf(b: Batch): string[] {
  return Object.keys(b.manifest.packages).sort();
}

/**
 * Scenarios that batches pinning different runtimes ran. Release builds have none: schema v1 does not
 * name a batch's target, so a release cannot establish that two targets have validated runs, and the
 * compare page stays hidden (only its placeholder is exported).
 */
export function comparisons(all: Batches): Comparison[] {
  if (isRelease()) return [];
  const byScenario = new Map<string, Batch[]>();
  for (const b of all.batches) {
    byScenario.set(b.record.scenario_sha256, [
      ...(byScenario.get(b.record.scenario_sha256) ?? []),
      b,
    ]);
  }
  return [...byScenario]
    .filter(([, batches]) => new Set(batches.map((b) => runtimeOf(b).join(" "))).size >= 2)
    .map(([scenario, batches]) => ({ scenario, batches, provenance: combined(batches) }))
    .sort((a, b) => a.scenario.localeCompare(b.scenario));
}
