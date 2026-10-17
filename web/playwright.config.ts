import { defineConfig, devices } from "@playwright/test";

/**
 * E2E against the running compose stack (API 8310, dev-oidc 8345) and this console on 8300.
 * `make up && make seed` first. The console is built and started by `webServer` unless one is
 * already listening on 8300.
 */
const SECRET = process.env.WEB_SESSION_SECRET ?? "e2e-only-session-secret-0123456789abcdef";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.WEB_PUBLIC_URL ?? "http://localhost:8300",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // `output: "standalone"`: run the standalone server (with its static assets), as in the image.
    command:
      "pnpm run build && cp -r .next/static .next/standalone/web/.next/ && mkdir -p public && cp -r public .next/standalone/web/ && PORT=8300 HOSTNAME=127.0.0.1 node .next/standalone/web/server.js",
    url: "http://127.0.0.1:8300",
    reuseExistingServer: true,
    timeout: 240_000,
    env: { WEB_SESSION_SECRET: SECRET, NODE_ENV: "production" },
  },
});
