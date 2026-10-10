import { expect, test } from "@playwright/test";

import { exportedPages } from "../../scripts/exported.ts";

// A build that read no artifacts exports each dynamic route once, at its labeled empty page. Those pages
// render no artifact values and no banner; scripts/release-guard.ts output passes on this build (CI
// runs it before these tests).

const EMPTY = ["/trials/none/none/", "/lineage/none/none/", "/findings/none/", "/compare/none/"];

test("every dynamic route exports only its empty page", () => {
  const pages = exportedPages();
  for (const path of EMPTY) expect(pages).toContain(path);
  expect(pages.filter((p) => /^\/(trials|lineage|findings|compare)\//.test(p)).sort()).toEqual(
    [...EMPTY].sort(),
  );
});

for (const path of exportedPages()) {
  test(`${path} renders no artifact values and no banner`, async ({ page }) => {
    const response = await page.goto(path, { waitUntil: "networkidle" });
    expect(response?.status()).toBe(200);
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.getByRole("note", { name: "Demo data" })).toHaveCount(0);
    expect(await page.locator("main").innerText()).not.toContain("DEMO-");
  });
}

test("the overview says there are no results yet", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Pre-results. No batch has been validated")).toBeVisible();
});

test("the lineage page's graph says it has no checkpoints", async ({ page }) => {
  await page.goto("/lineage/none/none/");
  await expect(page.locator(".react-flow")).toBeVisible();
  await expect(page.getByText("No checkpoints in this build.")).toBeVisible();
});
