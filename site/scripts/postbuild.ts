// Fixes the router's segment prefetch files after a static export on Windows.
//
// Next 16.3.6 names each prefetch file from a path.relative() result and replaces only "/" with "."
// (export/index.js, collectSegmentPaths). On Windows the path uses "\", so `matrix\__PAGE__` is written
// as the directory `__next.matrix/` holding `__PAGE__.txt`, while the client requests
// `__next.matrix.__PAGE__.txt` and gets a 404 on every prefetch. On Linux, where CI and releases build,
// the names are already right and this finds nothing to do.

import { readdirSync, renameSync, rmdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const OUT = fileURLToPath(new URL("../out/", import.meta.url));

function* files(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* files(full);
    else yield full;
  }
}

function removeEmptyDirs(dir: string): void {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) removeEmptyDirs(path.join(dir, entry.name));
  }
  if (readdirSync(dir).length === 0) rmdirSync(dir);
}

/** Moves every file under a `__next.*` directory to its dot-joined name in the route directory. */
export function flattenSegmentDirs(out: string): number {
  let moved = 0;
  const visit = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const full = path.join(dir, entry.name);
      if (!entry.name.startsWith("__next.")) {
        if (entry.name !== "_next") visit(full);
        continue;
      }
      for (const file of [...files(full)]) {
        const flat = path.relative(dir, file).split(path.sep).join(".");
        renameSync(file, path.join(dir, flat));
        moved += 1;
      }
      removeEmptyDirs(full);
    }
  };
  visit(out);
  return moved;
}

if (import.meta.main) {
  if (!statSync(OUT, { throwIfNoEntry: false })?.isDirectory()) {
    console.error("postbuild: out/ does not exist");
    process.exit(1);
  }
  const moved = flattenSegmentDirs(OUT);
  console.log(
    moved === 0
      ? "postbuild: segment prefetch files already flat"
      : `postbuild: flattened ${moved} segment prefetch files (Windows export naming)`,
  );
}
