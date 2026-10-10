import { defineConfig, devices } from "@playwright/test";

// CRASHCOURT_E2E_PORT lets two worktrees test at once. The server is never reused: a server already on
// the port may be serving another build, and the tests would then check the wrong pages.
export const PORT = Number(process.env.CRASHCOURT_E2E_PORT ?? 4173);

// No retries: a flaky browser test is a bug to fix, not to retry past.
export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  globalTimeout: 10 * 60_000,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `node scripts/serve.ts ${PORT}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
