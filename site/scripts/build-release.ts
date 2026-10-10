// Builds the explorer for release: `npm run build:release`. It reads ../results and nothing else, and
// stops at the first failed step:
//   1. lib/schema matches schemas/v1, and the verdict guard passes;
//   2. the release guard on ../results (scripts/release-guard.ts input);
//   3. a fresh `next build` with CRASHCOURT_RELEASE=1, so lib/artifacts.ts reads ../results only;
//   4. the post-build fix-up, then the release guard on out/ (scripts/release-guard.ts output).

import { spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = fileURLToPath(new URL("..", import.meta.url));

function step(script: string, ...args: string[]): void {
  const run = spawnSync(process.execPath, [script, ...args], {
    cwd: SITE,
    stdio: "inherit",
    env: { ...process.env, CRASHCOURT_RELEASE: "1" },
  });
  if (run.status !== 0) {
    console.error(
      `build:release: ${path.basename(script)} ${args.join(" ")} failed; nothing built`,
    );
    process.exit(run.status ?? 1);
  }
}

if (process.env.CRASHCOURT_ARTIFACTS) {
  console.error("build:release reads ../results only; unset CRASHCOURT_ARTIFACTS");
  process.exit(1);
}
step("scripts/gen-schema.ts", "--check");
step("scripts/guard-verdicts.ts");
step("scripts/release-guard.ts", "input");
rmSync(path.join(SITE, "out"), { recursive: true, force: true });
step("node_modules/next/dist/bin/next", "build");
step("scripts/postbuild.ts");
step("scripts/release-guard.ts", "output");
console.log("build:release: out/ holds the release build");
