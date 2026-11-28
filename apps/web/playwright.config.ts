import { defineConfig, devices } from "@playwright/test";

const baseURL = "http://127.0.0.1:3000";

export default defineConfig({
  testDir: "./e2e",
  outputDir: ".next/e2e-results",
  retries: process.env.CI ? 1 : 0,
  failOnFlakyTests: true,
  reporter: "list",
  use: { baseURL, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run start -- --hostname 127.0.0.1 --port 3000",
    url: baseURL,
    env: { API_INTERNAL_URL: "http://127.0.0.1:1" },
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
