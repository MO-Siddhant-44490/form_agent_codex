import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  retries: 0,
  workers: 1, // one persistent browser context with the extension loaded
  reporter: [["list"]],
  webServer: {
    command: "node ../fixtures/serve.mjs",
    url: "http://127.0.0.1:4173/basic-form/",
    reuseExistingServer: true,
  },
});
