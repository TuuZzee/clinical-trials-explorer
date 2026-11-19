import { defineConfig, devices } from "@playwright/test";
import { PRESETS } from "./lib/presets";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:3000",
    channel: process.env.PLAYWRIGHT_CHANNEL,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "node tests/backend-fixture.mjs",
      url: "http://127.0.0.1:9010/health",
      reuseExistingServer: false,
      env: {
        FIXTURE_QUERIES: JSON.stringify(PRESETS.map(({ id, request }) => [request.query, id])),
      },
    },
    {
      command: "npm run start -- --port 3000",
      url: "http://127.0.0.1:3000",
      reuseExistingServer: false,
      env: { BACKEND_URL: "http://127.0.0.1:9010" },
    },
  ],
});
