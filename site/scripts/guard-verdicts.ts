// Fails the build if site code could produce or pick a verdict on its own: the UI never computes one.
//
// What it catches. It reads text, not syntax, in every source file under site/ except tests and build
// output (.ts, .tsx, .mts, .cts, .js, .jsx, .mjs, .cjs, .json, and .css), anywhere but lib/tokens.ts:
// - an outcome name in quotes of any kind: the five verdicts, and the two outcomes that are not
//   verdicts (VIOLATION_CANDIDATE, NO_ANOMALY_UNSPECIFIED), which must never be turned into one;
// - the verdict list or the word Verdict (so a cast, an annotation, or an import of the type), including
//   in visible text, where tokens.ts must supply the word;
// - indexing into verdictLegend() or into VerdictProp.
//
// What it cannot catch: a name built at run time (concatenated, or read from data the site was not
// given as an artifact), a verdict picked from the legend by its token (a find on the glyph), or one
// derived at the type level (Parameters<typeof verdictToken>, or the generated Outcome type narrowed).
// It is a tripwire that review backs up, not a proof.
//
// Exempt: lib/tokens.ts, which maps a verdict to its glyph, label, and color; lib/schema/types.gen.ts and
// lib/schema/schemas.gen.ts, generated from schemas/v1, which declare the outcome vocabulary and fail
// `npm run check:schema` if edited; the top-level fixtures/, which is artifact data that Ajv validates
// against schemas/v1; and this file, which spells the patterns.

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = fileURLToPath(new URL("..", import.meta.url));
const EXTENSIONS = new Set([
  ".ts",
  ".tsx",
  ".mts",
  ".cts",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".json",
  ".css",
]);
/** Skipped by top-level name only: a `fixtures/` or `tests/` folder elsewhere is scanned. */
const SKIPPED_TOP = new Set([
  "node_modules",
  ".next",
  "out",
  "tests",
  "test-results",
  "playwright-report",
  ".lighthouse",
  ".budgets",
  "fixtures",
]);
const EXEMPT = new Set([
  "lib/tokens.ts",
  "lib/schema/types.gen.ts",
  "lib/schema/schemas.gen.ts",
  "scripts/guard-verdicts.ts",
]);

const NAMES = [
  "PASS",
  "EXPECTED",
  "GAP",
  "VIOLATION",
  "INVALID",
  "VIOLATION_CANDIDATE",
  "NO_ANOMALY_UNSPECIFIED",
].join("|");
const QUOTED_VERDICT = new RegExp(`(["'\`])(${NAMES})\\1`, "g");
const LIST = /\bVERDICTS\b/g;
const TYPE = /\bVerdict\b/g;
const INDEXED = /\b(verdictLegend\(\)|VerdictProp)\s*\[/g;

export interface Finding {
  readonly file: string;
  readonly line: number;
  readonly text: string;
  readonly reason: string;
}

export function scan(source: string, file: string): Finding[] {
  const findings: Finding[] = [];
  source.split("\n").forEach((text, i) => {
    const at = { file, line: i + 1, text: text.trim() };
    for (const m of text.matchAll(QUOTED_VERDICT)) {
      findings.push({ ...at, reason: `verdict name ${m[0]}` });
    }
    for (const m of text.matchAll(LIST))
      findings.push({ ...at, reason: `the verdict list (${m[0]})` });
    for (const m of text.matchAll(TYPE))
      findings.push({ ...at, reason: `the verdict type (${m[0]})` });
    for (const m of text.matchAll(INDEXED))
      findings.push({ ...at, reason: `indexing into the verdict vocabulary (${m[0]})` });
  });
  return findings;
}

function* sourceFiles(dir: string, top: boolean): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!(top && SKIPPED_TOP.has(entry.name))) yield* sourceFiles(full, false);
    } else if (EXTENSIONS.has(path.extname(entry.name))) {
      yield full;
    }
  }
}

export function scanSite(site: string = SITE): Finding[] {
  const findings: Finding[] = [];
  for (const file of sourceFiles(site, true)) {
    const rel = path.relative(site, file).split(path.sep).join("/");
    if (EXEMPT.has(rel)) continue;
    findings.push(...scan(readFileSync(file, "utf8"), rel));
  }
  return findings;
}

if (import.meta.main) {
  const findings = scanSite();
  if (findings.length > 0) {
    console.error("Site code must not decide verdicts; render the verdict an artifact carries.");
    for (const f of findings) console.error(`  ${f.file}:${f.line}: ${f.reason}\n    ${f.text}`);
    process.exit(1);
  }
  console.log(
    "guard-verdicts: no outcome names, verdict list, or verdict type outside lib/tokens.ts",
  );
}
