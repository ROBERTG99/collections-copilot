import { describe, expect, it } from "vitest";

import {
  hasRole,
  isExpired,
  needsRefresh,
  openOidcState,
  sealOidcState,
  type OidcState,
  type Session,
} from "@/lib/session";

const SECRET = "test-secret-that-is-at-least-32-characters-long";
const NOW_S = 1_790_000_000;

const session: Session = {
  accessToken: "header.payload.signature",
  refreshToken: "refresh-token",
  expiresAt: NOW_S + 3600,
  user: { username: "ana.analyst", roles: ["analyst"], tenantId: "t-1" },
};

describe("OIDC round-trip state cookie", () => {
  const state: OidcState = {
    state: "st-1",
    verifier: "verifier-abc",
    nonce: "nonce-xyz",
    returnTo: "/queue",
  };

  it("round-trips encrypted (the verifier never appears in clear)", async () => {
    const cookie = await sealOidcState(state, SECRET);
    expect(cookie).not.toContain("verifier-abc");
    expect(await openOidcState(cookie, SECRET)).toEqual(state);
  });

  it("opens to null when missing, malformed or sealed with another secret", async () => {
    const cookie = await sealOidcState(state, SECRET);
    expect(await openOidcState(undefined, SECRET)).toBeNull();
    expect(await openOidcState("garbage", SECRET)).toBeNull();
    expect(
      await openOidcState(cookie, "another-secret-that-is-also-32-characters-long"),
    ).toBeNull();
  });

  it("refuses short secrets when sealing", async () => {
    await expect(sealOidcState(state, "short")).rejects.toThrow(/32/);
  });
});

describe("expiry and refresh timing", () => {
  it("is expired at and after the access token expiry", () => {
    expect(isExpired(session, (NOW_S + 3599) * 1000)).toBe(false);
    expect(isExpired(session, (NOW_S + 3600) * 1000)).toBe(true);
    expect(isExpired(session, (NOW_S + 7200) * 1000)).toBe(true);
  });

  it("needs a refresh within the last minute before expiry", () => {
    expect(needsRefresh(session, NOW_S + 3600 - 61)).toBe(false);
    expect(needsRefresh(session, NOW_S + 3600 - 59)).toBe(true);
    expect(needsRefresh(session, NOW_S + 4000)).toBe(true);
  });
});

describe("hasRole", () => {
  it("matches any of the roles and is false without a session", () => {
    expect(hasRole(session, "analyst")).toBe(true);
    expect(hasRole(session, "admin", "analyst")).toBe(true);
    expect(hasRole(session, "admin")).toBe(false);
    expect(hasRole(null, "analyst")).toBe(false);
  });
});
