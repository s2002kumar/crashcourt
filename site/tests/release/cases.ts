// Inputs that prove the release guard: one clean case it must pass and planted cases it must refuse.
// The unit tests build each into a temporary directory; CI's site-release workflow builds one into
// ../results and runs the real release build on it.
//
//   node tests/release/cases.ts <case> <dir>      writes the case into dir (which must be empty)
//   node tests/release/cases.ts --list            prints each case and what the guard must do
//
// The clean case is a self-test batch made from the demo matrix batch: every DEMO- id renamed to
// selftest-, provenance "run", its own scenario hash, and hashes recomputed. It exists only for this
// test, is never committed under results/, and is not a result.

import { createHash } from "node:crypto";
import {
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { walk } from "../../lib/artifacts.ts";

const SITE = fileURLToPath(new URL("../..", import.meta.url));
export const FIXTURES = path.join(SITE, "fixtures");
const SOURCE = "DEMO-batch-matrix";
const NOTE = "Demo data, not a result: written by crashcourt fixtures --demo, not measured.";
const SELFTEST_NOTE = "Release guard self-test: made from the demo fixtures, never published.";
export const SELFTEST_SCENARIO = createHash("sha256")
  .update("crashcourt release guard self-test scenario")
  .digest("hex");

const sha256 = (bytes: Buffer | string) => createHash("sha256").update(bytes).digest("hex");

function editJson(file: string, edit: (value: Record<string, unknown>) => void): void {
  const value = JSON.parse(readFileSync(file, "utf8")) as Record<string, unknown>;
  edit(value);
  writeFileSync(file, JSON.stringify(value, null, 2) + "\n");
}

/** Points the batch record and every result at the batch's current manifest bytes. */
function rehash(batchDir: string): void {
  const digest = sha256(readFileSync(path.join(batchDir, "manifest.json")));
  for (const file of walk(batchDir)) {
    const name = path.basename(file);
    if (name === "batch.json" || name === "result.json") {
      editJson(file, (v) => {
        v.manifest_sha256 = digest;
      });
    }
  }
}

/** The clean self-test batch, written to dir/selftest-batch-matrix. */
function clean(dir: string): string {
  const batchDir = path.join(dir, "selftest-batch-matrix");
  for (const file of walk(path.join(FIXTURES, SOURCE))) {
    const rel = path.relative(path.join(FIXTURES, SOURCE), file).replaceAll("DEMO-", "selftest-");
    const text = readFileSync(file, "utf8")
      .replaceAll("DEMO-", "selftest-")
      .replaceAll(NOTE, SELFTEST_NOTE)
      .replaceAll('"provenance": "fixture"', '"provenance": "run"');
    mkdirSync(path.dirname(path.join(batchDir, rel)), { recursive: true });
    writeFileSync(path.join(batchDir, rel), text);
  }
  editJson(path.join(batchDir, "batch.json"), (v) => {
    v.scenario_sha256 = SELFTEST_SCENARIO;
  });
  rehash(batchDir);
  return batchDir;
}

function firstFile(dir: string, name: string): string {
  const found = [...walk(dir)].sort().find((f) => path.basename(f) === name);
  if (!found) throw new Error(`no ${name} under ${dir}`);
  return found;
}

export interface Case {
  /** What the release guard must do with this input. */
  readonly expect: "pass" | "fail";
  /** What a refusal must say, in part. */
  readonly because?: RegExp;
  readonly build: (dir: string) => void;
}

export const CASES: Readonly<Record<string, Case>> = {
  // A harness-shaped batch with provenance run and no DEMO- anywhere.
  clean: { expect: "pass", build: (dir) => void clean(dir) },
  // A fixture batch copied into results/ as it is.
  "planted-fixture": {
    expect: "fail",
    because: /provenance fixture, not run/,
    build: (dir) =>
      cpSync(path.join(FIXTURES, SOURCE), path.join(dir, SOURCE), { recursive: true }),
  },
  // One DEMO- id inside an otherwise valid run batch.
  "demo-id-in-run-batch": {
    expect: "fail",
    because: /merged\.jsonl: holds "DEMO-"/,
    build: (dir) => {
      const merged = firstFile(clean(dir), "merged.jsonl");
      const text = readFileSync(merged, "utf8");
      writeFileSync(merged, text.replace("selftest-op-1", "DEMO-op-1"));
    },
  },
  // A fixture renamed and relabeled as a run, its demonstration scenario hash kept.
  "relabeled-fixture": {
    expect: "fail",
    because: /scenario_sha256 is a demonstration scenario's/,
    build: (dir) => {
      const batchDir = clean(dir);
      const fixture = JSON.parse(
        readFileSync(path.join(FIXTURES, SOURCE, "batch.json"), "utf8"),
      ) as { scenario_sha256: string };
      editJson(path.join(batchDir, "batch.json"), (v) => {
        v.scenario_sha256 = fixture.scenario_sha256;
      });
    },
  },
  // The manifest changed after the batch recorded its hash.
  "manifest-mismatch": {
    expect: "fail",
    because: /manifest_sha256 is not the sha256 of/,
    build: (dir) => {
      editJson(path.join(clean(dir), "manifest.json"), (v) => {
        v.runner = "github-hosted";
      });
    },
  },
  // A result whose manifest hash is not its batch's.
  "result-from-another-manifest": {
    expect: "fail",
    because: /manifest_sha256 differs from its batch's/,
    build: (dir) => {
      editJson(firstFile(clean(dir), "result.json"), (v) => {
        v.manifest_sha256 = sha256("another manifest");
      });
    },
  },
  // An empty results/: the release fails closed.
  "no-batch": { expect: "fail", because: /holds no batch/, build: () => {} },
};

if (import.meta.main && process.argv[2] === "--list") {
  for (const [name, c] of Object.entries(CASES)) console.log(`${name} ${c.expect}`);
} else if (import.meta.main) {
  const [name, dir] = process.argv.slice(2);
  const chosen = name ? CASES[name] : undefined;
  if (!chosen || !dir) {
    console.error(`usage: node tests/release/cases.ts <${Object.keys(CASES).join("|")}> <dir>`);
    process.exit(2);
  }
  if (existsSync(dir) && readdirSync(dir).length > 0) {
    console.error(`${dir} is not empty; a release case is built into an empty directory`);
    process.exit(2);
  }
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  chosen.build(dir);
  console.log(`release case ${name} (the guard must ${chosen.expect}) written to ${dir}`);
}
