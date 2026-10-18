import { createHash } from "node:crypto";

import { exportJWK, generateKeyPair, SignJWT, UnsecuredJWT, type JWK } from "jose";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { webConfig, type WebConfig } from "@/lib/config";
import {
  authorizeUrl,
  exchangeCode,
  pkceChallenge,
  randomToken,
  refreshSession,
  sessionFromTokens,
  verifyIdToken,
} from "@/lib/oidc";
import type { Session } from "@/lib/session";

const ISSUER = "http://dev-oidc:8345";
const PUBLIC_ISSUER = "http://localhost:8345";
const cfg: WebConfig = webConfig({
  WEB_OIDC_ISSUER: ISSUER,
  WEB_OIDC_PUBLIC_ISSUER: PUBLIC_ISSUER,
  WEB_OIDC_CLIENT_ID: "cc-web",
  WEB_PUBLIC_URL: "http://localhost:8300",
} as unknown as NodeJS.ProcessEnv);

/** An unsigned access token: the BFF reads its claims (the API verifies it). */
function accessToken(claims: Record<string, unknown>): string {
  return new UnsecuredJWT(claims).encode();
}

let signingKey: CryptoKey;
let otherKey: CryptoKey;
let jwk: JWK;

beforeAll(async () => {
  const pair = await generateKeyPair("RS256");
  signingKey = pair.privateKey;
  otherKey = (await generateKeyPair("RS256")).privateKey;
  jwk = { ...(await exportJWK(pair.publicKey)), kid: "k1", alg: "RS256", use: "sig" };
});

function idToken(
  claims: Record<string, unknown>,
  opts: { issuer?: string; audience?: string; key?: CryptoKey } = {},
): Promise<string> {
  return new SignJWT(claims)
    .setProtectedHeader({ alg: "RS256", kid: "k1" })
    .setIssuer(opts.issuer ?? ISSUER)
    .setAudience(opts.audience ?? "cc-web")
    .setSubject("user-1")
    .setIssuedAt()
    .setExpirationTime("5m")
    .sign(opts.key ?? signingKey);
}

type Call = { url: string; init?: RequestInit };

/** A fake issuer: serves the JWKS and answers the token endpoint with `token`. */
function fakeIssuer(token: () => Response | Promise<Response>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = input instanceof Request ? input.url : String(input);
    calls.push({ url, init });
    if (url === cfg.endpoints.jwks) {
      return Response.json({ keys: [jwk] });
    }
    if (url === cfg.endpoints.token) return token();
    return new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, tokenCalls: () => calls.filter((c) => c.url === cfg.endpoints.token) };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("PKCE", () => {
  it("derives the S256 challenge as unpadded base64url(SHA-256(verifier))", async () => {
    const verifier = "dBjftJeZ4CVP-mJ92K1dKsCsJUB3RDcuQbdbRyv5HuU";
    const expected = createHash("sha256").update(verifier).digest("base64url");
    const challenge = await pkceChallenge(verifier);
    expect(challenge).toBe(expected);
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/); // 32 bytes, no "=" padding
  });

  it("makes random URL-safe tokens of the requested entropy", () => {
    const a = randomToken();
    const b = randomToken();
    expect(a).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(a).not.toBe(b);
    expect(randomToken(16)).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });
});

describe("authorizeUrl", () => {
  it("asks for an authorization code with PKCE S256, state and nonce", () => {
    const url = new URL(authorizeUrl(cfg, { state: "st", challenge: "ch", nonce: "nn" }));
    expect(`${url.origin}${url.pathname}`).toBe(`${PUBLIC_ISSUER}/authorize`);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      response_type: "code",
      client_id: "cc-web",
      redirect_uri: "http://localhost:8300/auth/callback",
      scope: "openid profile email",
      state: "st",
      nonce: "nn",
      code_challenge: "ch",
      code_challenge_method: "S256",
    });
  });
});

describe("sessionFromTokens", () => {
  it("reads the user, roles and tenant from the access token", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-18T12:00:00Z"));
    const token = accessToken({
      sub: "sub-1",
      "cognito:username": "ana.analyst",
      "cognito:groups": ["analyst", "supervisor"],
      "custom:tenant_id": "tenant-1",
    });
    const session = sessionFromTokens({
      access_token: token,
      refresh_token: "r1",
      expires_in: 900,
    });
    expect(session).toEqual({
      accessToken: token,
      refreshToken: "r1",
      expiresAt: Date.parse("2026-10-18T12:15:00Z") / 1000,
      user: { username: "ana.analyst", roles: ["analyst", "supervisor"], tenantId: "tenant-1" },
    });
  });

  it("falls back to sub, no roles, the previous refresh token and one hour", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-18T12:00:00Z"));
    const session = sessionFromTokens(
      { access_token: accessToken({ sub: "sub-1", "cognito:groups": "analyst" }), expires_in: 0 },
      "previous-refresh",
    );
    expect(session.user).toEqual({ username: "sub-1", roles: [], tenantId: "" });
    expect(session.refreshToken).toBe("previous-refresh");
    expect(session.expiresAt).toBe(Date.parse("2026-10-18T13:00:00Z") / 1000);
    expect(
      sessionFromTokens({ access_token: accessToken({}), expires_in: 60 }).refreshToken,
    ).toBeNull();
  });
});

describe("verifyIdToken", () => {
  it("accepts a token signed by the issuer's JWKS, for this client, with the nonce", async () => {
    const { calls } = fakeIssuer(() => new Response(null, { status: 500 }));
    await expect(verifyIdToken(cfg, await idToken({ nonce: "n-1" }), "n-1")).resolves.toBe(
      undefined,
    );
    expect(calls.some((c) => c.url === "http://dev-oidc:8345/.well-known/jwks.json")).toBe(true);
  });

  it("accepts the public issuer name as well", async () => {
    fakeIssuer(() => new Response(null, { status: 500 }));
    const token = await idToken({ nonce: "n-2" }, { issuer: PUBLIC_ISSUER });
    await expect(verifyIdToken(cfg, token, "n-2")).resolves.toBe(undefined);
  });

  it("rejects a token whose nonce does not match (a replayed or injected code)", async () => {
    fakeIssuer(() => new Response(null, { status: 500 }));
    await expect(verifyIdToken(cfg, await idToken({ nonce: "other" }), "n-1")).rejects.toThrow(
      "ID token nonce does not match",
    );
    await expect(verifyIdToken(cfg, await idToken({}), "n-1")).rejects.toThrow(/nonce/);
  });

  it("rejects a token issued for another client", async () => {
    fakeIssuer(() => new Response(null, { status: 500 }));
    const token = await idToken({ nonce: "n-1" }, { audience: "another-client" });
    await expect(verifyIdToken(cfg, token, "n-1")).rejects.toThrow(/aud/);
  });

  it("rejects a token from another issuer", async () => {
    fakeIssuer(() => new Response(null, { status: 500 }));
    const token = await idToken({ nonce: "n-1" }, { issuer: "http://evil.example" });
    await expect(verifyIdToken(cfg, token, "n-1")).rejects.toThrow(/iss/);
  });

  it("rejects a token not signed by the issuer's key", async () => {
    fakeIssuer(() => new Response(null, { status: 500 }));
    const token = await idToken({ nonce: "n-1" }, { key: otherKey });
    await expect(verifyIdToken(cfg, token, "n-1")).rejects.toThrow(/signature/);
  });
});

describe("exchangeCode", () => {
  it("posts the code and verifier to the token endpoint and verifies the ID token", async () => {
    const access = accessToken({ "cognito:username": "ana.analyst", "custom:tenant_id": "t-1" });
    const id = await idToken({ nonce: "n-1" });
    const issuer = fakeIssuer(() =>
      Response.json({ access_token: access, id_token: id, refresh_token: "r1", expires_in: 3600 }),
    );
    const session = await exchangeCode(cfg, "code-1", "verifier-1", "n-1");
    expect(session.accessToken).toBe(access);
    expect(session.refreshToken).toBe("r1");
    expect(session.user.username).toBe("ana.analyst");

    const [call] = issuer.tokenCalls();
    expect(call?.init?.method).toBe("POST");
    expect(call?.init?.cache).toBe("no-store");
    expect(new Headers(call?.init?.headers).get("content-type")).toBe(
      "application/x-www-form-urlencoded",
    );
    expect(Object.fromEntries(new URLSearchParams(String(call?.init?.body)))).toEqual({
      client_id: "cc-web",
      grant_type: "authorization_code",
      code: "code-1",
      code_verifier: "verifier-1",
      redirect_uri: "http://localhost:8300/auth/callback",
    });
  });

  it("refuses a token response without an ID token", async () => {
    fakeIssuer(() => Response.json({ access_token: accessToken({}), expires_in: 3600 }));
    await expect(exchangeCode(cfg, "code", "v", "n")).rejects.toThrow(
      "the issuer returned no ID token",
    );
  });

  it("refuses a session when the nonce does not match", async () => {
    const id = await idToken({ nonce: "someone-else" });
    fakeIssuer(() =>
      Response.json({ access_token: accessToken({}), id_token: id, expires_in: 3600 }),
    );
    await expect(exchangeCode(cfg, "code", "v", "n-1")).rejects.toThrow(/nonce/);
  });

  it("surfaces a token endpoint error", async () => {
    fakeIssuer(() => new Response("invalid_grant", { status: 400 }));
    await expect(exchangeCode(cfg, "code", "v", "n")).rejects.toThrow(
      "token endpoint returned 400",
    );
  });
});

describe("refreshSession", () => {
  const base: Session = {
    accessToken: "old",
    refreshToken: null,
    expiresAt: 0,
    user: { username: "ana.analyst", roles: ["analyst"], tenantId: "t-1" },
  };

  it("does nothing without a refresh token", async () => {
    const issuer = fakeIssuer(() => Response.json({}));
    expect(await refreshSession(cfg, base)).toBeNull();
    expect(issuer.tokenCalls()).toHaveLength(0);
  });

  it("shares one token request between concurrent refreshes (single-use refresh tokens)", async () => {
    const access = accessToken({ "cognito:username": "ana.analyst" });
    let release: (r: Response) => void = () => undefined;
    const issuer = fakeIssuer(() => new Promise<Response>((resolve) => (release = resolve)));
    const session = { ...base, refreshToken: "rt-concurrent" };
    const first = refreshSession(cfg, session);
    const second = refreshSession(cfg, session);
    expect(second).toBe(first);
    release(Response.json({ access_token: access, expires_in: 3600 }));
    const [a, b] = await Promise.all([first, second]);
    expect(a).toEqual(b);
    expect(a?.accessToken).toBe(access);
    expect(a?.refreshToken).toBe("rt-concurrent"); // kept when the issuer does not rotate it
    expect(issuer.tokenCalls()).toHaveLength(1);
    expect(
      Object.fromEntries(new URLSearchParams(String(issuer.tokenCalls()[0]?.init?.body))),
    ).toEqual({ client_id: "cc-web", grant_type: "refresh_token", refresh_token: "rt-concurrent" });
  });

  it("reuses a just-finished refresh for a minute, then asks again", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-18T12:00:00Z"));
    const issuer = fakeIssuer(() =>
      Response.json({ access_token: accessToken({}), refresh_token: "rt-new", expires_in: 3600 }),
    );
    const session = { ...base, refreshToken: "rt-reuse" };
    const first = await refreshSession(cfg, session);
    expect(first?.refreshToken).toBe("rt-new");

    vi.setSystemTime(new Date("2026-10-18T12:00:59Z"));
    expect(await refreshSession(cfg, session)).toEqual(first); // an old cookie in flight
    expect(issuer.tokenCalls()).toHaveLength(1);

    vi.setSystemTime(new Date("2026-10-18T12:01:01Z"));
    await refreshSession(cfg, session);
    expect(issuer.tokenCalls()).toHaveLength(2);
  });

  it("returns null when the issuer refuses the refresh", async () => {
    fakeIssuer(() => new Response("invalid_grant", { status: 400 }));
    expect(await refreshSession(cfg, { ...base, refreshToken: "rt-refused" })).toBeNull();
  });
});
