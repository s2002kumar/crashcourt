// Build-time access to harness artifacts. Every file the site reads goes through a schema, so a malformed
// or unrecognized artifact fails the build instead of rendering.

import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import {
  ArtifactError,
  parseBatch,
  parseEvents,
  parseManifest,
  parseResult,
} from "./schema/validate.ts";

/**
 * The artifact directory: $CRASHCOURT_ARTIFACTS if set, else site/fixtures when it exists, else none.
 * Release builds point it at ../results (F-02 adds the fixtures and the release guard).
 */
export function artifactRoot(cwd: string = process.cwd()): string | null {
  const configured = process.env.CRASHCOURT_ARTIFACTS;
  if (configured) return path.resolve(cwd, configured);
  const fixtures = path.join(cwd, "fixtures");
  return existsSync(fixtures) ? fixtures : null;
}

export interface ValidationSummary {
  readonly files: number;
  readonly events: number;
  readonly manifests: number;
  readonly batches: number;
  readonly results: number;
}

type Counts = Omit<ValidationSummary, "files">;
const NONE: Counts = { events: 0, manifests: 0, batches: 0, results: 0 };

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else yield full;
  }
}

/**
 * Validates one artifact by its name: `manifest.json`, `batch.json`, `result.json`, or a `.jsonl`
 * history. Anything else is refused.
 */
export function validateArtifact(file: string): Counts {
  const text = readFileSync(file, "utf8");
  switch (path.basename(file)) {
    case "manifest.json":
      parseManifest(text, file);
      return { ...NONE, manifests: 1 };
    case "batch.json":
      parseBatch(text, file);
      return { ...NONE, batches: 1 };
    case "result.json":
      parseResult(text, file);
      return { ...NONE, results: 1 };
  }
  if (file.endsWith(".jsonl")) return { ...NONE, events: parseEvents(text, file).length };
  throw new ArtifactError(file, undefined, [
    "no schema covers this file, so the site cannot read it",
  ]);
}

/** Validates every file under `root`, collecting all failures before throwing. */
export function validateArtifactTree(root: string): ValidationSummary {
  const failures: ArtifactError[] = [];
  let files = 0;
  const total = { ...NONE };
  for (const file of walk(root)) {
    files += 1;
    try {
      const counts = validateArtifact(file);
      total.events += counts.events;
      total.manifests += counts.manifests;
      total.batches += counts.batches;
      total.results += counts.results;
    } catch (e) {
      if (!(e instanceof ArtifactError)) throw e;
      failures.push(e);
    }
  }
  if (failures.length > 0) {
    throw new AggregateError(failures, failures.map((f) => f.message).join("\n"));
  }
  return { files, ...total };
}
