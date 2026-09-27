// Build-time access to harness artifacts. Every file the site reads goes through a schema, so a malformed
// or unrecognized artifact fails the build instead of rendering.

import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { ArtifactError, parseEvents, parseManifest } from "./schema/validate.ts";

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
}

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else yield full;
  }
}

/** Validates one artifact by its name: `manifest.json` or a `.jsonl` history. Anything else is refused. */
export function validateArtifact(file: string): { events: number; manifests: number } {
  const text = readFileSync(file, "utf8");
  if (path.basename(file) === "manifest.json") {
    parseManifest(text, file);
    return { events: 0, manifests: 1 };
  }
  if (file.endsWith(".jsonl")) return { events: parseEvents(text, file).length, manifests: 0 };
  throw new ArtifactError(file, undefined, [
    "no schema covers this file, so the site cannot read it",
  ]);
}

/** Validates every file under `root`, collecting all failures before throwing. */
export function validateArtifactTree(root: string): ValidationSummary {
  const failures: ArtifactError[] = [];
  let files = 0;
  let events = 0;
  let manifests = 0;
  for (const file of walk(root)) {
    files += 1;
    try {
      const counts = validateArtifact(file);
      events += counts.events;
      manifests += counts.manifests;
    } catch (e) {
      if (!(e instanceof ArtifactError)) throw e;
      failures.push(e);
    }
  }
  if (failures.length > 0) {
    throw new AggregateError(failures, failures.map((f) => f.message).join("\n"));
  }
  return { files, events, manifests };
}
