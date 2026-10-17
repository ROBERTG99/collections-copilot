/**
 * BFF session: the browser holds only an encrypted, httpOnly cookie (JWE `dir` + A256GCM).
 * Tokens never reach client-side JavaScript. A tampered, foreign or stale cookie opens to null.
 */
import { EncryptJWT, jwtDecrypt } from "jose";

export const SESSION_COOKIE = "cc_session";
export const OIDC_COOKIE = "cc_oidc";
/** A session can be refreshed for this long after the access token expired. */
export const REFRESH_GRACE_S = 12 * 3600;

export type SessionUser = {
  username: string;
  roles: string[];
  tenantId: string;
};

export type Session = {
  accessToken: string;
  refreshToken: string | null;
  expiresAt: number; // access token expiry, epoch seconds
  user: SessionUser;
};

async function keyFrom(secret: string): Promise<Uint8Array> {
  if (secret.length < 32) {
    throw new Error("WEB_SESSION_SECRET must be at least 32 characters");
  }
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(secret));
  return new Uint8Array(digest);
}

export async function sealSession(session: Session, secret: string): Promise<string> {
  return new EncryptJWT({ s: session })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime(session.expiresAt + REFRESH_GRACE_S)
    .encrypt(await keyFrom(secret));
}

export async function openSession(
  cookie: string | undefined,
  secret: string,
): Promise<Session | null> {
  if (!cookie) return null;
  try {
    const { payload } = await jwtDecrypt(cookie, await keyFrom(secret));
    const session = payload.s as Session | undefined;
    if (!session || typeof session.accessToken !== "string" || !session.user) return null;
    return session;
  } catch {
    return null; // tampered, wrong key, malformed or expired
  }
}

/** Short-lived state for the authorization-code + PKCE round trip. */
export type OidcState = { state: string; verifier: string; nonce: string; returnTo: string };

export async function sealOidcState(value: OidcState, secret: string): Promise<string> {
  return new EncryptJWT({ o: value })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime("10m")
    .encrypt(await keyFrom(secret));
}

export async function openOidcState(
  cookie: string | undefined,
  secret: string,
): Promise<OidcState | null> {
  if (!cookie) return null;
  try {
    const { payload } = await jwtDecrypt(cookie, await keyFrom(secret));
    return (payload.o as OidcState | undefined) ?? null;
  } catch {
    return null;
  }
}

/** The access token has expired (the session may still be refreshable within the grace). */
export function isExpired(session: Session, nowMs: number = Date.now()): boolean {
  return session.expiresAt <= Math.floor(nowMs / 1000);
}

export function needsRefresh(session: Session, now = Date.now() / 1000): boolean {
  return session.expiresAt - now < 60;
}

export function hasRole(session: Session | null, ...roles: string[]): boolean {
  return !!session && session.user.roles.some((r) => roles.includes(r));
}
