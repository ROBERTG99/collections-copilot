import path from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname) } },
  test: {
    include: ["tests/unit/**/*.test.ts", "tests/unit/**/*.test.tsx"],
    environment: "node",
    // West of UTC on purpose: date-only values once rendered a day early (M4 review).
    env: { TZ: "America/New_York" },
    coverage: {
      provider: "v8",
      include: ["lib/**/*.ts", "components/**/*.tsx"],
      exclude: ["lib/api/schema.d.ts"],
      reporter: ["text-summary", "lcov"],
    },
  },
});
