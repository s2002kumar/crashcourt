// Runs Lighthouse (default mobile profile, simulated throttling) against the static export and asserts
// the spec's targets: performance at least 90 on /, and accessibility 100 on every route. The overview
// runs three times and is judged on its median performance score. Reports go to .lighthouse/.
//
//   npm run build && npm run lighthouse

import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as chromeLauncher from "chrome-launcher";
import lighthouse from "lighthouse";

import { serve } from "./serve.ts";

const SITE = fileURLToPath(new URL("..", import.meta.url));
const REPORTS = path.join(SITE, ".lighthouse");
const PORT = 4176;

const TARGETS: readonly {
  page: string;
  runs: number;
  performance?: number;
  accessibility: number;
}[] = [
  { page: "/", runs: 3, performance: 0.9, accessibility: 1 },
  { page: "/matrix/", runs: 1, accessibility: 1 },
  { page: "/trials/none/none/", runs: 1, accessibility: 1 },
  { page: "/lineage/none/none/", runs: 1, accessibility: 1 },
  { page: "/findings/none/", runs: 1, accessibility: 1 },
  { page: "/methodology/", runs: 1, accessibility: 1 },
];

const median = (xs: number[]): number => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]!;

mkdirSync(REPORTS, { recursive: true });
const server = await serve(PORT);
const chrome = await chromeLauncher.launch({ chromeFlags: ["--headless=new", "--no-sandbox"] });
const failures: string[] = [];
const summary: Record<string, unknown>[] = [];
try {
  for (const target of TARGETS) {
    const scores: { performance: number; accessibility: number }[] = [];
    for (let run = 1; run <= target.runs; run++) {
      const result = await lighthouse(`http://127.0.0.1:${PORT}${target.page}`, {
        port: chrome.port,
        output: "html",
        logLevel: "error",
        onlyCategories: ["performance", "accessibility", "best-practices"],
      });
      if (!result) throw new Error(`no Lighthouse result for ${target.page}`);
      const { categories } = result.lhr;
      scores.push({
        performance: categories.performance?.score ?? 0,
        accessibility: categories.accessibility?.score ?? 0,
      });
      const name = target.page.replace(/\//g, "_").replace(/^_|_$/g, "") || "overview";
      writeFileSync(path.join(REPORTS, `${name}-${run}.html`), result.report as string);
    }
    const performance = median(scores.map((s) => s.performance));
    const accessibility = Math.min(...scores.map((s) => s.accessibility));
    summary.push({ page: target.page, runs: target.runs, performance, accessibility });
    console.log(
      `${target.page.padEnd(22)} performance ${Math.round(performance * 100)}  accessibility ${Math.round(accessibility * 100)}`,
    );
    if (target.performance !== undefined && performance < target.performance) {
      failures.push(`${target.page}: performance ${performance} < ${target.performance}`);
    }
    if (accessibility < target.accessibility) {
      failures.push(`${target.page}: accessibility ${accessibility} < ${target.accessibility}`);
    }
  }
} finally {
  try {
    chrome.kill();
  } catch (e) {
    // On Windows Chrome can still hold its temporary profile when chrome-launcher deletes it. The
    // browser has stopped; only that cleanup failed.
    if ((e as NodeJS.ErrnoException).code !== "EPERM") throw e;
  }
  server.close();
}
writeFileSync(path.join(REPORTS, "summary.json"), JSON.stringify(summary, null, 2) + "\n");
if (failures.length > 0) {
  console.error(`Lighthouse targets missed:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
