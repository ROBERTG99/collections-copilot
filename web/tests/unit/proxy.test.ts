import { describe, expect, it } from "vitest";

import { apiTarget, readCapped } from "@/lib/proxy";
import { webConfig } from "@/lib/config";

const BASE = "http://api:8310";

describe("BFF proxy target (M6 security audit L1)", () => {
  it("maps console paths under /api/v1", () => {
    expect(apiTarget(BASE, ["queue"], "?limit=20")).toBe("http://api:8310/api/v1/queue?limit=20");
    expect(apiTarget(BASE, ["cases", "abc", "approval"], "")).toBe(
      "http://api:8310/api/v1/cases/abc/approval",
    );
  });

  it.each([
    [["webhooks", "twilio", "inbound"]],
    [["Webhooks", "twilio"]],
    [["webhooks/twilio/inbound"]], // %2F decoded into one segment
    [[".", "webhooks"]],
    [[".."]],
    [["queue", ""]],
    [["a\\b"]],
    [[]],
  ])("refuses %j", (path) => {
    expect(apiTarget(BASE, path as string[], "")).toBeNull();
  });
});

describe("BFF body cap (M6 security audit L2)", () => {
  it("reads small bodies and refuses large ones without buffering them whole", async () => {
    const small = new Request("http://x", { method: "POST", body: "a".repeat(10) });
    expect((await readCapped(small, 100))?.byteLength).toBe(10);
    const big = new Request("http://x", { method: "POST", body: "a".repeat(200) });
    expect(await readCapped(big, 100)).toBeNull();
  });
});

describe("dev session secret (M6 security audit M9)", () => {
  it("is refused on a public URL even with the escape hatch", () => {
    expect(() =>
      webConfig({
        NODE_ENV: "production",
        WEB_PUBLIC_URL: "https://collections.example.com",
        WEB_SESSION_SECRET: "dev-only-secret-0123456789abcdef0123456789",
        WEB_ALLOW_DEV_SECRET: "true",
      } as NodeJS.ProcessEnv),
    ).toThrow(/WEB_SESSION_SECRET/);
    expect(() =>
      webConfig({
        NODE_ENV: "production",
        WEB_PUBLIC_URL: "http://localhost:8300",
        WEB_SESSION_SECRET: "dev-only-secret-0123456789abcdef0123456789",
        WEB_ALLOW_DEV_SECRET: "true",
      } as NodeJS.ProcessEnv),
    ).not.toThrow();
  });
});
