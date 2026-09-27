import { expect, test, type Page } from "@playwright/test";

import { ROUTES } from "./routes.ts";

/**
 * Loads a page and fails on console errors, error responses, failed requests, or any request that
 * leaves the site. Chromium reports every HEAD request as aborted once its headers arrive (the router
 * sends HEAD before prefetching a page), so HEAD is judged by its response status alone.
 */
async function open(page: Page, path: string): Promise<void> {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  page.on("response", (r) => {
    if (r.status() >= 400) problems.push(`${r.status()}: ${r.url()}`);
  });
  page.on("requestfailed", (r) => {
    if (r.method() !== "HEAD") problems.push(`failed: ${r.url()} (${r.failure()?.errorText})`);
  });
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.origin !== "http://127.0.0.1:4173" && url.protocol !== "data:") {
      problems.push(`left the site: ${r.url()}`);
    }
  });
  const response = await page.goto(path, { waitUntil: "networkidle" });
  expect(response?.status(), path).toBe(200);
  expect(problems, path).toEqual([]);
}

for (const { name, path } of ROUTES) {
  test(`${name} renders its page and nothing else`, async ({ page }) => {
    await open(page, path);
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page).toHaveTitle(/Crashcourt/);
  });

  test(`${name}: every focusable element shows a focus ring`, async ({ page }) => {
    await open(page, path);
    const seen = new Set<string>();
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press("Tab");
      const focus = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        // A visually hidden control (the theme radios) shows its ring on its label.
        const shown = el.getBoundingClientRect().width <= 1 ? (el.closest("label") ?? el) : el;
        const style = getComputedStyle(shown);
        return {
          id: `${el.tagName}:${el.textContent?.trim().slice(0, 30) ?? ""}:${el.getAttribute("href") ?? ""}`,
          outline: style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2,
        };
      });
      if (focus === null || seen.has(focus.id)) break;
      seen.add(focus.id);
      expect(focus.outline, `focus ring on ${focus.id}`).toBe(true);
    }
    expect(seen.size).toBeGreaterThan(3);
  });
}

test("the skip link is the first stop and moves focus to the content", async ({ page }) => {
  await open(page, "/matrix/");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
});

test("the current page is marked in the navigation", async ({ page }) => {
  await open(page, "/methodology/");
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Methodology" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(nav.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
});

test("a theme choice applies at once and survives a reload", async ({ page }) => {
  await open(page, "/");
  const theme = page.getByRole("group", { name: "Color theme" });
  // The radios are visually hidden; a reader clicks their labels.
  const choose = async (name: string) => {
    await theme.getByText(name, { exact: true }).click();
    await expect(theme.getByRole("radio", { name })).toBeChecked();
  };
  const background = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);

  await choose("Light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const light = await background();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(theme.getByRole("radio", { name: "Light" })).toBeChecked();
  await choose("Dark");
  expect(await background()).not.toBe(light);
  await choose("Auto");
  await expect(page.locator("html")).not.toHaveAttribute("data-theme");
});

test("? opens the keyboard list and Escape closes it", async ({ page }) => {
  await open(page, "/");
  await page.keyboard.press("?");
  const dialog = page.getByRole("dialog", { name: "Keyboard" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: /Keys/ }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: /Keys/ })).toBeFocused();
});

test("the timeline zooms with its buttons", async ({ page }) => {
  await open(page, "/trials/none/none/");
  const zoom = page.getByRole("group", { name: "Timeline zoom" });
  await expect(zoom.getByRole("button", { name: "Zoom out" })).toBeDisabled();
  await zoom.getByRole("button", { name: "Zoom in" }).click();
  await expect(zoom.getByRole("button", { name: "Zoom out" })).toBeEnabled();
  await zoom.getByRole("button", { name: "Reset" }).click();
  await expect(zoom.getByRole("button", { name: "Zoom out" })).toBeDisabled();
});

test("the lineage graph loads after the page", async ({ page }) => {
  await open(page, "/lineage/none/none/");
  await expect(page.locator(".react-flow")).toBeVisible();
  await expect(page.getByText("No checkpoints in this build.")).toBeVisible();
});

test("reduced motion removes transitions", async ({ browser }) => {
  const duration = async (reducedMotion: "reduce" | "no-preference") => {
    const context = await browser.newContext({ reducedMotion });
    const page = await context.newPage();
    await open(page, "/");
    const seconds = await page
      .getByRole("group", { name: "Color theme" })
      .getByText("Dark", { exact: true })
      .evaluate((el) => parseFloat(getComputedStyle(el).transitionDuration));
    await context.close();
    return seconds;
  };
  expect(await duration("no-preference")).toBeGreaterThan(0.05);
  expect(await duration("reduce")).toBeLessThan(0.001);
});

test("an unknown address gets the 404 page", async ({ page }) => {
  const response = await page.goto("/no/such/page/");
  expect(response?.status()).toBe(404);
  await expect(page.locator("h1")).toHaveText("Nothing is published at this address");
});
