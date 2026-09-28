import { defineConfig, devices } from "@playwright/test";

// End-to-end tests drive the dashboard in a real browser against a running TestMind stack
// (API, worker, database). TESTMIND_WEB_URL: the dashboard (vite preview or the web container).
export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  expect: { timeout: 60_000 },
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.TESTMIND_WEB_URL ?? "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
