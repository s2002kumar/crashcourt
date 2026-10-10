// The pages a static export holds, read from out/. Which ids a dynamic route exports depends on the
// artifacts the build read, so the browser tests, the bundle measurement, Lighthouse, and the
// screenshots find their pages here instead of naming them.

import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { walk } from "../lib/artifacts.ts";

export const OUT = fileURLToPath(new URL("../out/", import.meta.url));

/** The first exported page of a route pattern (directories in name order), as a path ending in "/". */
export function firstPage(pattern: string, out: string = OUT): string {
  let dirs = [""];
  for (const segment of pattern.split("/").filter(Boolean)) {
    dirs = dirs.flatMap((d) =>
      segment.startsWith("[")
        ? readdirSync(path.join(out, d), { withFileTypes: true })
            .filter((e) => e.isDirectory() && !e.name.startsWith("__next"))
            .map((e) => `${d}/${e.name}`)
            .sort()
        : [`${d}/${segment}`],
    );
  }
  const page = dirs.find((d) => existsSync(path.join(out, d, "index.html")));
  if (page === undefined) throw new Error(`no exported page for ${pattern}`);
  return `${page}/`;
}

/** Every page the export serves, as paths ending in "/", except the 404 page. */
export function exportedPages(out: string = OUT): string[] {
  const pages: string[] = [];
  for (const file of walk(out)) {
    if (path.basename(file) !== "index.html") continue;
    const dir = path.relative(out, path.dirname(file)).split(path.sep).join("/");
    if (dir === "404" || dir === "_not-found" || dir.startsWith("_next")) continue;
    pages.push(dir === "" ? "/" : `/${dir}/`);
  }
  return pages.sort();
}
