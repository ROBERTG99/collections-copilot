import { UnsecuredJWT } from "jose";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activeSession, clearSession, currentSession, writeSession } from "@/lib/server-session";
import {
  openSession,
  REFRESH_GRACE_S,
  sealSession,
  SESSION_COOKIE,
  type Session,
} from "@/lib/session";

const jar = vi.hoisted(() => {
  const values = new Map<string, string>();
  return {
    values,
    get: vi.fn((name: string) =>
      values.has(name) ? { name, value: values.get(name) } : undefined,
    ),
    set: vi.fn<(name: string, value: string, options?: Record<string, unknown>) => void>(
      (name, value) => {
        values.set(name, value);
      },
    ),
    delete: vi.fn((name: string) => void values.delete(name)),
  };
});
vi.mock("next/headers", () => ({ cookies: async () => jar }));

const SECRET = "test-secret-that-is-at-least-32-characters-long";
const NOW = new Date("2026-10-18T12:00:00Z");
const NOW_S = NOW.getTime() / 1000;

const session: Session = {
  accessToken: "header.payload.signature",
  refreshToken: "rt-1",
  expiresAt: NOW_S + 3600,
  user: { username: "ana.analyst", roles: ["analyst"], tenantId: "t-1" },
};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  vi.stubEnv("WEB_SESSION_SECRET", SECRET);
  vi.stubEnv("WEB_PUBLIC_URL", "https://collections.example.com");
  vi.stubEnv("WEB_OIDC_ISSUER", "http://dev-oidc:8345");
  jar.values.clear();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("writeSession", () => {
  it("sets an encrypted, httpOnly, lax, site-wide cookie that outlives the token by the grace", async () => {
    await writeSession(session);
    expect(jar.set).toHaveBeenCalledTimes(1);
    const [name, value, options] = jar.set.mock.calls[0] ?? [];
    expect(name).toBe(SESSION_COOKIE);
    expect(value).not.toContain("ana.analyst");
    expect(await openSession(value, SECRET)).toEqual(session);
    expect(options).toEqual({
      httpOnly: true,
      secure: true, // https public URL
      sameSite: "lax",
      path: "/",
      maxAge: 3600 + REFRESH_GRACE_S,
    });
  });

  it("is not Secure on a plain-http local URL and never lives under a minute", async () => {
    vi.stubEnv("WEB_PUBLIC_URL", "http://localhost:8300");
    await writeSession({ ...session, expiresAt: NOW_S - REFRESH_GRACE_S - 3600 });
    const options = jar.set.mock.calls[0]?.[2];
    expect(options?.secure).toBe(false);
    expect(options?.maxAge).toBe(60);
  });
});

describe("currentSession and clearSession", () => {
  it("opens the cookie from the jar, and is null without one", async () => {
    expect(await currentSession()).toBeNull();
    jar.values.set(SESSION_COOKIE, await sealSession(session, SECRET));
    expect(await currentSession()).toEqual(session);
    expect(jar.get).toHaveBeenCalledWith(SESSION_COOKIE);
  });

  it("deletes the session cookie", async () => {
    jar.values.set(SESSION_COOKIE, await sealSession(session, SECRET));
    await clearSession();
    expect(jar.delete).toHaveBeenCalledWith(SESSION_COOKIE);
    expect(await currentSession()).toBeNull();
  });
});

describe("activeSession", () => {
  it("is null when signed out", async () => {
    expect(await activeSession()).toBeNull();
  });

  it("returns a fresh session as is, without calling the issuer", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    jar.values.set(SESSION_COOKIE, await sealSession(session, SECRET));
    expect(await activeSession()).toEqual(session);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(jar.set).not.toHaveBeenCalled();
  });

  it("refreshes a session about to expire and writes the new cookie", async () => {
    const access = new UnsecuredJWT({ "cognito:username": "ana.analyst" }).encode();
    const fetchMock = vi.fn(async () =>
      Response.json({ access_token: access, refresh_token: "rt-2", expires_in: 900 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const expiring = { ...session, refreshToken: "rt-expiring", expiresAt: NOW_S + 30 };
    jar.values.set(SESSION_COOKIE, await sealSession(expiring, SECRET));

    const refreshed = await activeSession();
    expect(refreshed?.accessToken).toBe(access);
    expect(refreshed?.refreshToken).toBe("rt-2");
    expect(refreshed?.expiresAt).toBe(NOW_S + 900);
    expect(fetchMock).toHaveBeenCalledWith("http://dev-oidc:8345/token", expect.anything());
    expect(await openSession(jar.values.get(SESSION_COOKIE), SECRET)).toEqual(refreshed);
  });

  it("is null (and leaves the cookie alone) when the refresh fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("invalid_grant", { status: 400 })),
    );
    const expiring = { ...session, refreshToken: "rt-dead", expiresAt: NOW_S + 30 };
    jar.values.set(SESSION_COOKIE, await sealSession(expiring, SECRET));
    expect(await activeSession()).toBeNull();
    expect(jar.set).not.toHaveBeenCalled();
  });
});
