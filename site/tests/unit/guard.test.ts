import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, test } from "node:test";

import { scan, scanSite } from "../../scripts/guard-verdicts.ts";

// The verdict names are assembled at run time so this file does not trip the guard's own patterns if
// the guard is ever widened to scan tests.
const V = (name: string): string => name;
const q = (name: string, quote = '"'): string => quote + V(name) + quote;

describe("guard-verdicts", () => {
  test("the site has no verdict logic", () => {
    assert.deepEqual(scanSite(), []);
  });

  const planted: [string, string][] = [
    ["an assignment", `const verdict = k > 0 ? ${q("VIOLATION")} : ${q("PASS")};`],
    ["a comparison", `if (cell.verdict === ${q("GAP", "'")}) return null;`],
    ["a JSX attribute", `<VerdictBadge verdict=${q("EXPECTED")} />`],
    ["a template literal", `const v = ${q("INVALID", "`")};`],
    ["a cast", "const v = row.status as Verdict;"],
    ["a satisfies clause", "const v = x satisfies Verdict;"],
    ["an annotation", "const v: Verdict = pick(row);"],
    ["a parameter type", "function badge(verdict: Verdict) {}"],
    ["an import of the type", `import type { Verdict } from "@/lib/tokens";`],
    ["indexing into the list", "const v = VERDICTS[3];"],
    ["a JSON value", `{ "outcome": ${q("VIOLATION")} }`],
    ["a candidate promoted", `if (r.outcome === ${q("VIOLATION_CANDIDATE")}) return badge;`],
    ["no anomaly read as a pass", `r.outcome === ${q("NO_ANOMALY_UNSPECIFIED", "'")} ? a : b`],
    ["indexing into the legend", "const v = verdictLegend()[3]!.verdict;"],
    ["a type picked from the props", 'let v: VerdictProp["verdict"];'],
    ["a CSS selector", `[data-outcome=${q("INVALID")}] { display: none; }`],
  ];
  for (const [what, source] of planted) {
    test(`catches ${what}`, () => {
      assert.ok(scan(source, "app/page.tsx").length > 0, source);
    });
  }

  test("reports file and line", () => {
    const [finding] = scan(`ok\nconst v = ${q("PASS")};`, "components/x.tsx");
    assert.equal(finding?.file, "components/x.tsx");
    assert.equal(finding?.line, 2);
  });

  const allowed = [
    "A GAP is not a bug; it means no clause covers the behavior.",
    `const label = token.label;`,
    `const passes = "PASSED";`,
    `import type { VerdictProp } from "@/lib/tokens";`,
    `export function VerdictBadge({ verdict }: VerdictProp) {}`,
    `{verdictLegend().map(({ verdict }) => <VerdictBadge verdict={verdict} />)}`,
  ];
  for (const source of allowed) {
    test(`allows: ${source}`, () => assert.deepEqual(scan(source, "app/page.tsx"), []));
  }

  test("cannot see a name built at run time or picked by its token, which is why review backs it up", () => {
    assert.deepEqual(scan(`const v = "VIOL" + "ATION";`, "app/page.tsx"), []);
    const byGlyph = `verdictLegend().find(({ token }) => token.glyph === "cross")`;
    assert.deepEqual(scan(byGlyph, "app/page.tsx"), []);
  });

  test("scans every source file, .json included; skips tests, generated schemas, fixtures", () => {
    const site = mkdtempSync(path.join(tmpdir(), "cc-guard-"));
    const files: Record<string, string> = {
      "app/data.json": `{ "v": ${q("PASS")} }`,
      "scripts/tool.mts": "const v = VERDICTS[0];",
      "lib/x.cts": "let v: Verdict;",
      "tests/unit/x.test.ts": `const v = ${q("GAP")};`,
      "lib/schema/types.gen.ts": `export type Outcome = ${q("PASS")} | ${q("GAP")};`,
      "lib/schema/outcomes.gen.ts": `export const PASSING = ${q("PASS")};`,
      "fixtures/b/result.json": `{ "outcome": ${q("PASS")} }`,
      "components/fixtures/demo.tsx": `const v = ${q("VIOLATION")};`,
      "app/tests/page.tsx": `const v = ${q("GAP")};`,
      "styles/x.css": `[data-v=${q("PASS")}] {}`,
      "lib/tokens.ts": `const VERDICTS = [${q("PASS")}] as const;`,
    };
    try {
      for (const [name, content] of Object.entries(files)) {
        mkdirSync(path.dirname(path.join(site, name)), { recursive: true });
        writeFileSync(path.join(site, name), content);
      }
      const flagged = [...new Set(scanSite(site).map((f) => f.file))].sort();
      assert.deepEqual(flagged, [
        "app/data.json",
        "app/tests/page.tsx",
        "components/fixtures/demo.tsx",
        "lib/schema/outcomes.gen.ts",
        "lib/x.cts",
        "scripts/tool.mts",
        "styles/x.css",
      ]);
    } finally {
      rmSync(site, { recursive: true });
    }
  });
});
