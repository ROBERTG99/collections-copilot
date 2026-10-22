import { describe, expect, it } from "vitest";

import { correlationId, traceparent } from "@/lib/trace";

describe("BFF trace headers", () => {
  it("starts a W3C trace context the API continues", () => {
    const value = traceparent();
    expect(value).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
    expect(value.split("-")[1]).not.toBe("0".repeat(32));
    expect(traceparent()).not.toBe(value);
  });

  it("keeps a safe client correlation id and replaces anything else", () => {
    expect(correlationId("abc-123")).toBe("abc-123");
    expect(correlationId(null)).toMatch(/^[0-9a-f-]{36}$/);
    expect(correlationId("bad id\r\nx-injected: 1")).toMatch(/^[0-9a-f-]{36}$/);
    expect(correlationId("x".repeat(200))).toMatch(/^[0-9a-f-]{36}$/);
  });
});
