import { describe, expect, it } from "vitest";

import { openSession, sealSession, type Session } from "@/lib/session";

const SECRET = "test-secret-that-is-at-least-32-characters-long";
const session: Session = {
  accessToken: "header.payload.signature",
  refreshToken: "refresh-token",
  expiresAt: Math.floor(Date.now() / 1000) + 3600,
  user: {
    username: "ana.analyst",
    roles: ["analyst"],
    tenantId: "5d52f1a8-7b91-5150-919c-ce3a391e5ea8",
  },
};

describe("session cookie", () => {
  it("round-trips an encrypted session", async () => {
    const cookie = await sealSession(session, SECRET);
    expect(cookie).not.toContain("ana.analyst"); // encrypted, not just signed
    expect(await openSession(cookie, SECRET)).toEqual(session);
  });

  it("rejects a tampered cookie", async () => {
    const cookie = await sealSession(session, SECRET);
    const parts = cookie.split(".");
    const ciphertext = parts[3] ?? "";
    const flipped = (ciphertext[0] === "A" ? "B" : "A") + ciphertext.slice(1);
    const tampered = [...parts.slice(0, 3), flipped, parts[4]].join(".");
    expect(await openSession(tampered, SECRET)).toBeNull();
  });

  it("rejects a cookie sealed with another secret", async () => {
    const cookie = await sealSession(session, "another-secret-that-is-also-32-characters-long");
    expect(await openSession(cookie, SECRET)).toBeNull();
  });

  it("rejects garbage and missing cookies", async () => {
    expect(await openSession("not-a-jwe", SECRET)).toBeNull();
    expect(await openSession(undefined, SECRET)).toBeNull();
  });

  it("rejects a session whose refresh window has passed", async () => {
    const stale = { ...session, expiresAt: Math.floor(Date.now() / 1000) - 13 * 3600 };
    const cookie = await sealSession(stale, SECRET);
    expect(await openSession(cookie, SECRET)).toBeNull();
  });

  it("refuses short secrets", async () => {
    await expect(sealSession(session, "short")).rejects.toThrow(/32/);
  });
});
