// Captures the screenshots DESIGN.md shows (docs/design/). Run after `npm run build`.
//
//   node scripts/screenshots.ts [outDir]

import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

import { firstPage } from "./exported.ts";
import { serve } from "./serve.ts";

type Shot = { name: string; url: string; theme: "dark" | "light"; phone?: boolean };

const SHOTS: readonly Shot[] = [
  { name: "overview-dark", url: "/", theme: "dark" },
  { name: "overview-light", url: "/", theme: "light" },
  { name: "overview-phone-dark", url: "/", theme: "dark", phone: true },
  { name: "matrix-light", url: "/matrix/", theme: "light" },
  { name: "trial-dark", url: firstPage("/trials/[cell]/[trial]"), theme: "dark" },
  { name: "lineage-dark", url: firstPage("/lineage/[trial]/[thread]"), theme: "dark" },
  { name: "finding-dark", url: firstPage("/findings/[id]"), theme: "dark" },
  { name: "methodology-light", url: "/methodology/", theme: "light" },
];

const outDir = path.resolve(
  process.argv[2] ?? fileURLToPath(new URL("../docs/design/", import.meta.url)),
);
mkdirSync(outDir, { recursive: true });

const port = 4174;
const server = await serve(port);
const browser = await chromium.launch();
try {
  for (const shot of SHOTS) {
    const context = await browser.newContext({
      viewport: shot.phone ? { width: 390, height: 844 } : { width: 1280, height: 860 },
      deviceScaleFactor: 1,
      colorScheme: shot.theme,
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${port}${shot.url}`, { waitUntil: "networkidle" });
    if (shot.url.startsWith("/lineage/")) await page.locator(".react-flow").waitFor();
    await page.screenshot({ path: path.join(outDir, `${shot.name}.png`), fullPage: true });
    await context.close();
  }
} finally {
  await browser.close();
  server.close();
}
console.log(`${SHOTS.length} screenshots in ${outDir}`);
