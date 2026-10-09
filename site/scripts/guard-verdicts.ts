// Fails the build if site code could produce or pick a verdict on its own: the UI never computes one.
//
// What it catches. It reads text, not syntax, in every source file under site/ except tests and
// generated output (.ts, .tsx, .mts, .cts, .js, .jsx, .mjs, .cjs, and .json): a verdict name in quotes
// of any kind, the list of verdicts, and the name of the verdict type, anywhere but lib/tokens.ts. So a
// literal, a comparison, a cast, an annotation, an import of the type, or indexing into the list fails.
//
// What it cannot catch: a name built at run time (concatenated, or read from data the site was not
// given as an artifact). It is a tripwire that review backs up, not a proof.
//
// Exempt: lib/tokens.ts, which maps a verdict to its glyph, label, and color; lib/schema/*.gen.ts,
// generated from schemas/v1, which declare the outcome vocabulary and fail `npm run check:schema` if
// edited; fixtures/, which is artifact data that Ajv validates; and this file, which spells the patterns.

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = fileURLToPath(new URL("..", import.meta.url));
const EXTENSIONS = new Set([".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".json"]);
const SKIPPED_DIRS = new Set([
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
const EXEMPT = new Set(["lib/tokens.ts", "scripts/guard-verdicts.ts"]);
const GENERATED = /^lib\/schema\/[^/]+\.gen\.ts$/;

const NAMES = ["PASS", "EXPECTED", "GAP", "VIOLATION", "INVALID"].join("|");
const QUOTED_VERDICT = new RegExp(`(["'\`])(${NAMES})\\1`, "g");
const LIST = /\bVERDICTS\b/g;
const TYPE = /\bVerdict\b/g;

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
  });
  return findings;
}

function* sourceFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIPPED_DIRS.has(entry.name)) yield* sourceFiles(full);
    } else if (EXTENSIONS.has(path.extname(entry.name))) {
      yield full;
    }
  }
}

export function scanSite(site: string = SITE): Finding[] {
  const findings: Finding[] = [];
  for (const file of sourceFiles(site)) {
    const rel = path.relative(site, file).split(path.sep).join("/");
    if (EXEMPT.has(rel) || GENERATED.test(rel)) continue;
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
  console.log("guard-verdicts: no verdict names, list, or type outside lib/tokens.ts");
}
