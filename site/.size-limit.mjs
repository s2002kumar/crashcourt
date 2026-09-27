// size-limit checks, built from the committed limits (budgets.json) and the file lists that
// `npm run measure` records for each route (.budgets/measure.json). Run after build and measure.

import { readFileSync } from "node:fs";

const budgets = JSON.parse(readFileSync("budgets.json", "utf8")).routes;
const measured = JSON.parse(readFileSync(".budgets/measure.json", "utf8")).routes;

for (const pattern of Object.keys(measured)) {
  if (!budgets[pattern])
    throw new Error(`${pattern} was measured but has no budget in budgets.json`);
}

const checks = [];
for (const [pattern, limits] of Object.entries(budgets)) {
  const route = measured[pattern];
  if (!route) throw new Error(`budgets.json names ${pattern}, which was not measured`);
  for (const [kind, kb] of Object.entries(limits)) {
    checks.push({
      name: `${pattern} ${kind} JS`,
      path: route[kind].files.map((f) => `out/${f}`),
      gzip: true,
      limit: `${kb} kB`,
    });
  }
}

export default checks;
