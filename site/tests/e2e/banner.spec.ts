import { expect, test, type Page } from "@playwright/test";

import { exportedPages } from "../../scripts/exported.ts";
import { ROUTES } from "./routes.ts";

// The demo banner, on every page the export holds (read from out/, not a list of routes). A page shows
// the banner exactly when it renders a fixture value, and every fixture id begins with DEMO-, so on
// every page: DEMO- in the content if and only if the banner is there. A build from the demo fixtures
// renders fixture values on every page, so there the banner must be on all of them.

const PAGES = exportedPages();

/** True when the page is wider than the viewport: a long id that does not wrap. */
const overflows = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);

test("this build exports every experience's page", () => {
  for (const { path } of ROUTES) expect(PAGES).toContain(path);
});

for (const colorScheme of ["dark", "light"] as const) {
  test.describe(`${colorScheme} theme`, () => {
    test.use({ colorScheme, viewport: { width: 1280, height: 640 } });

    for (const path of PAGES) {
      test(`${path} shows the demo banner exactly when it shows fixture data`, async ({ page }) => {
        const response = await page.goto(path, { waitUntil: "networkidle" });
        expect(response?.status(), path).toBe(200);
        expect(await overflows(page), "the page scrolls sideways").toBe(false);
        const banner = page.getByRole("note", { name: "Demo data" });
        const content = await page.locator("main").innerText();
        const demo = content.includes("DEMO-");
        await expect(banner).toHaveCount(demo ? 1 : 0);
        if (!demo) return;
        await expect(banner).toBeVisible();
        await expect(banner).toContainText("Demo data — not a result.");
        // Non-dismissible: nothing in it can be pressed.
        await expect(banner.locator("a, button, input, [role=button], [tabindex]")).toHaveCount(0);
        // Persistent: still on screen at the bottom of the page.
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        await expect(banner).toBeInViewport();
      });
    }
  });
}

test("a build from the demo fixtures shows the banner on every page", async ({ page }) => {
  const missing: string[] = [];
  for (const path of PAGES) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(200);
    if ((await page.getByRole("note", { name: "Demo data" }).count()) !== 1) missing.push(path);
  }
  expect(missing).toEqual([]);
});

test("no page scrolls sideways on a phone", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const wide: string[] = [];
  for (const path of PAGES) {
    await page.goto(path);
    if (await overflows(page)) wide.push(path);
  }
  await context.close();
  expect(wide).toEqual([]);
});
