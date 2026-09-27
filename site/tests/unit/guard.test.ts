import assert from "node:assert/strict";
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
    `import type { Verdict } from "@/lib/tokens";`,
    `function badge(verdict: Verdict) {}`,
  ];
  for (const source of allowed) {
    test(`allows: ${source}`, () => assert.deepEqual(scan(source, "app/page.tsx"), []));
  }
});
