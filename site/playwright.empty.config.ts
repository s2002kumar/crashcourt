import { defineConfig } from "@playwright/test";

import base from "./playwright.config.ts";

// The browser tests for a build that read no artifacts (CRASHCOURT_ARTIFACTS pointed at an empty
// directory): every dynamic route exports only its empty-state page.
export default defineConfig({ ...base, testDir: "tests/e2e-empty" });
