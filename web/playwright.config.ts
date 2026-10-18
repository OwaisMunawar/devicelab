import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.DEVICELAB_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
    // Locally, PW_CHANNEL=chrome reuses the installed Chrome instead of downloading Chromium.
    ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
  },
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
});
