import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/healthz/route";

describe("GET /healthz", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("reports ok when the BFF settings load", async () => {
    vi.stubEnv("NODE_ENV", "test");
    const res = GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok" });
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("fails when production runs with the dev session secret", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("WEB_SESSION_SECRET", "dev-only-secret");
    vi.stubEnv("WEB_ALLOW_DEV_SECRET", "false");
    const res = GET();
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ status: "misconfigured" });
  });
});
