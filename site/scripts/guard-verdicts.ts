// Fails the build if site code could decide a verdict (spec section 17, rule 1).
//
// A verdict reaches a page only as a value read from a validated artifact. To produce or compare one,
// code would have to write a verdict name as a string ("PASS") or cast something to the Verdict type.
// This guard rejects both everywhere except lib/tokens.ts, which maps a verdict to its glyph, label, and
// color. It matches text, not syntax: a quoted verdict name in a comment also fails, so it errs toward
// failing the build.

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = fileURLToPath(new URL("..", import.meta.url));
const SCANNED_DIRS = ["app", "components", "lib"];
const EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);
const ALLOWED = new Set(["lib/tokens.ts"]);

const QUOTED_VERDICT = /(["'`])(PASS|EXPECTED|GAP|VIOLATION|INVALID)\1/g;
const VERDICT_CAST = /\b(as|satisfies)\s+Verdict\b|<Verdict>/g;

export interface Finding {
  readonly file: string;
  readonly line: number;
  readonly text: string;
  readonly reason: string;
}

export function scan(source: string, file: string): Finding[] {
  const findings: Finding[] = [];
  source.split("\n").forEach((text, i) => {
    for (const m of text.matchAll(QUOTED_VERDICT)) {
      findings.push({ file, line: i + 1, text: text.trim(), reason: `verdict name ${m[0]}` });
    }
    for (const m of text.matchAll(VERDICT_CAST)) {
      findings.push({
        file,
        line: i + 1,
        text: text.trim(),
        reason: `cast to a verdict (${m[0]})`,
      });
    }
  });
  return findings;
}

function* sourceFiles(dir: string): Generator<string> {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* sourceFiles(full);
    else if (EXTENSIONS.has(path.extname(entry.name))) yield full;
  }
}

export function scanSite(site: string = SITE): Finding[] {
  const findings: Finding[] = [];
  for (const dir of SCANNED_DIRS) {
    for (const file of sourceFiles(path.join(site, dir))) {
      const rel = path.relative(site, file).split(path.sep).join("/");
      if (ALLOWED.has(rel)) continue;
      findings.push(...scan(readFileSync(file, "utf8"), rel));
    }
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
  console.log(`guard-verdicts: no verdict logic in ${SCANNED_DIRS.join(", ")}`);
}
