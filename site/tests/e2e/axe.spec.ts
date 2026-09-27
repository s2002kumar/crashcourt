import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ROUTES } from "./routes.ts";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

async function blocking(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map(
      (v) =>
        `${v.impact} ${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`,
    );
}

for (const colorScheme of ["dark", "light"] as const) {
  test.describe(`${colorScheme} theme`, () => {
    test.use({ colorScheme });

    for (const { name, path } of ROUTES) {
      test(`${name} has no serious or critical axe violations`, async ({ page }) => {
        await page.goto(path, { waitUntil: "networkidle" });
        if (path.startsWith("/lineage/")) await page.locator(".react-flow").waitFor();
        expect(await blocking(page)).toEqual([]);
      });
    }

    test("the keyboard dialog has no serious or critical axe violations", async ({ page }) => {
      await page.goto("/", { waitUntil: "networkidle" });
      await page.keyboard.press("?");
      await page.getByRole("dialog").waitFor();
      expect(await blocking(page)).toEqual([]);
    });
  });
}
