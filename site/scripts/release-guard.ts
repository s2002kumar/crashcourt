// The release guard: a release ships harness runs and nothing else. `npm run build:release`
// (scripts/build-release.ts) runs it on the input before the build and on the export after it.
//
//   node scripts/release-guard.ts input     checks ../results
//   node scripts/release-guard.ts output    checks out/
//
// Input. ../results must hold at least one batch: with none, the release fails closed. Every file must
// be a v1 artifact, and every rule lib/batches.ts checks must hold (each batch's manifest hash, each
// result's batch, manifest hash, and provenance, each event's trial). Then:
// - every batch and result record says provenance "run";
// - no file holds "DEMO-" anywhere, so a fixture id cannot hide inside an otherwise valid batch;
// - no batch names the scenario hash or the manifest hash of a committed fixture batch, so a fixture
//   relabeled as a run is still refused. The guard reads site/fixtures for those hashes; the build
//   itself never does.
//
// Output. No file under out/ holds "DEMO-", the demo banner's marker attribute, or a serialized
// fixture provenance, and /compare exports only its placeholder: v1 cannot name a batch's target, so a
// release cannot show that two targets have validated runs.

import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { walk } from "../lib/artifacts.ts";
import { loadBatches, type Batches } from "../lib/batches.ts";
import { DEMO_BANNER_ATTRIBUTE } from "../lib/provenance.ts";
import { EMPTY_SEGMENT } from "../lib/routes.ts";

const SITE = fileURLToPath(new URL("..", import.meta.url));
export const RESULTS = path.resolve(SITE, "..", "results");
export const FIXTURES = path.join(SITE, "fixtures");
export const OUT = path.join(SITE, "out");

const DEMO = "DEMO-";
/** A fixture's provenance as JSON writes it, and as it appears escaped inside an HTML payload. */
const FIXTURE_PROVENANCE = ['"provenance":"fixture"', '\\"provenance\\":\\"fixture\\"'];

const rel = (root: string, file: string) => path.relative(root, file).split(path.sep).join("/");

function load(root: string, problems: string[]): Batches | null {
  try {
    return loadBatches(root);
  } catch (e) {
    if (!(e instanceof AggregateError)) throw e;
    problems.push(
      ...(e.errors.length > 0 ? e.errors.map((x) => (x as Error).message) : [e.message]),
    );
    return null;
  }
}

/** Every reason the artifacts in `results` cannot be released; empty means they can. */
export function checkInput(results: string = RESULTS, fixtures: string = FIXTURES): string[] {
  if (!existsSync(results)) {
    return [`${results} does not exist; a release needs at least one harness batch`];
  }
  const problems: string[] = [];
  for (const file of walk(results)) {
    if (readFileSync(file).includes(DEMO)) {
      problems.push(`${rel(results, file)}: holds "${DEMO}", the prefix of every fixture id`);
    }
  }
  const loaded = load(results, problems);
  if (loaded === null) return problems;
  if (loaded.batches.length === 0) {
    problems.push(`${results} holds no batch; a release needs at least one harness batch`);
  }
  for (const s of [...loaded.batches, ...loaded.trials]) {
    if (s.provenance !== "run") problems.push(`${s.dir}: provenance ${s.provenance}, not run`);
  }
  const demo = load(fixtures, problems);
  if (demo === null) {
    problems.push(`cannot read the fixtures in ${fixtures}, so cannot refuse their hashes`);
    return problems;
  }
  const scenarios = new Set(demo.batches.map((b) => b.record.scenario_sha256));
  const manifests = new Set(demo.batches.map((b) => b.record.manifest_sha256));
  for (const b of loaded.batches) {
    if (scenarios.has(b.record.scenario_sha256)) {
      problems.push(`${b.dir}: scenario_sha256 is a demonstration scenario's (a fixture)`);
    }
    if (manifests.has(b.record.manifest_sha256)) {
      problems.push(`${b.dir}: manifest_sha256 is a fixture batch's manifest`);
    }
  }
  return problems;
}

/** Every trace of demo data in the export, and any compare page beyond the placeholder. */
export function checkOutput(out: string = OUT): string[] {
  if (!existsSync(out)) return [`${out} does not exist; build first`];
  const problems: string[] = [];
  const markers = [DEMO, DEMO_BANNER_ATTRIBUTE, ...FIXTURE_PROVENANCE];
  for (const file of walk(out)) {
    const bytes = readFileSync(file);
    for (const marker of markers) {
      if (bytes.includes(marker)) problems.push(`${rel(out, file)}: holds ${marker}`);
    }
  }
  const compare = path.join(out, "compare");
  if (existsSync(compare)) {
    const pages = readdirSync(compare, { withFileTypes: true })
      .filter((e) => e.isDirectory() && !e.name.startsWith("__next"))
      .map((e) => e.name);
    for (const page of pages) {
      if (page !== EMPTY_SEGMENT) problems.push(`compare/${page}/: compare is hidden in a release`);
    }
  }
  return problems;
}

if (import.meta.main) {
  const which = process.argv[2];
  if (which !== "input" && which !== "output") {
    console.error("usage: node scripts/release-guard.ts input|output");
    process.exit(2);
  }
  const problems = which === "input" ? checkInput() : checkOutput();
  if (problems.length > 0) {
    console.error(`release guard (${which}): ${problems.length} problem(s); nothing is released`);
    for (const p of problems) console.error(`  ${p}`);
    process.exit(1);
  }
  console.log(
    which === "input"
      ? `release guard (input): ${RESULTS} holds only harness runs`
      : `release guard (output): ${OUT} holds no demo data, and compare is hidden`,
  );
}
