import path from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname),
      // `server-only` throws outside a React Server Components bundle; tests import those modules.
      "server-only": path.resolve(import.meta.dirname, "tests/support/server-only.ts"),
    },
  },
  test: {
    include: ["tests/unit/**/*.test.ts", "tests/unit/**/*.test.tsx"],
    environment: "node",
    // West of UTC on purpose: date-only values once rendered a day early.
    env: { TZ: "America/New_York" },
    coverage: {
      provider: "v8",
      include: ["lib/**/*.ts", "components/**/*.tsx"],
      exclude: ["lib/api/schema.d.ts"],
      reporter: ["text-summary", "lcov"],
      thresholds: { lines: 70 },
    },
  },
});
