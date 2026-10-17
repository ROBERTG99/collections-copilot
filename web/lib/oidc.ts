/** Authorization code + PKCE (S256) against Cognito or dev-oidc. Server-side only. */
import "server-only";

import { createRemoteJWKSet, decodeJwt, jwtVerify } from "jose";

import type { WebConfig } from "@/lib/config";
import type { Session } from "@/lib/session";

function b64url(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString("base64url");
}

export function randomToken(bytes = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

export async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return b64url(new Uint8Array(digest));
}

export function authorizeUrl(
  cfg: WebConfig,
  params: { state: string; challenge: string; nonce: string },
): string {
  const query = new URLSearchParams({
    response_type: "code",
    client_id: cfg.clientId,
    redirect_uri: `${cfg.publicUrl}/auth/callback`,
    scope: "openid profile email",
    state: params.state,
    nonce: params.nonce,
    code_challenge: params.challenge,
    code_challenge_method: "S256",
  });
  return `${cfg.oidcPublicIssuer}/authorize?${query.toString()}`;
}

type TokenResponse = {
  access_token: string;
  id_token?: string;
  refresh_token?: string;
  expires_in: number;
};

/** The token response came over a server-to-server TLS channel from the issuer, so its claims
 * are read without re-verifying here; the API verifies every access token on every request. */
export function sessionFromTokens(tokens: TokenResponse, previousRefresh?: string | null): Session {
  const claims = decodeJwt(tokens.access_token);
  const roles = Array.isArray(claims["cognito:groups"])
    ? (claims["cognito:groups"] as string[])
    : [];
  return {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token ?? previousRefresh ?? null,
    expiresAt: Math.floor(Date.now() / 1000) + Number(tokens.expires_in || 3600),
    user: {
      username: String(claims["cognito:username"] ?? claims.sub ?? ""),
      roles,
      tenantId: String(claims["custom:tenant_id"] ?? ""),
    },
  };
}

async function tokenRequest(cfg: WebConfig, body: Record<string, string>): Promise<TokenResponse> {
  const resp = await fetch(`${cfg.oidcIssuer}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: cfg.clientId, ...body }),
    cache: "no-store",
  });
  if (!resp.ok) throw new Error(`token endpoint returned ${resp.status}`);
  return (await resp.json()) as TokenResponse;
}

const jwksByIssuer = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

/** The ID token from the code exchange must be signed by the issuer, be for this client, and
 * carry the nonce sent with the authorization request (replayed or injected codes fail). */
export async function verifyIdToken(cfg: WebConfig, idToken: string, nonce: string): Promise<void> {
  let jwks = jwksByIssuer.get(cfg.oidcIssuer);
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(`${cfg.oidcIssuer}/.well-known/jwks.json`));
    jwksByIssuer.set(cfg.oidcIssuer, jwks);
  }
  const { payload } = await jwtVerify(idToken, jwks, {
    audience: cfg.clientId,
    issuer: [cfg.oidcIssuer, cfg.oidcPublicIssuer],
  });
  if (payload.nonce !== nonce) throw new Error("ID token nonce does not match");
}

export async function exchangeCode(
  cfg: WebConfig,
  code: string,
  verifier: string,
  nonce: string,
): Promise<Session> {
  const tokens = await tokenRequest(cfg, {
    grant_type: "authorization_code",
    code,
    code_verifier: verifier,
    redirect_uri: `${cfg.publicUrl}/auth/callback`,
  });
  if (!tokens.id_token) throw new Error("the issuer returned no ID token");
  await verifyIdToken(cfg, tokens.id_token, nonce);
  return sessionFromTokens(tokens);
}

/** In-flight and just-finished refreshes by the refresh token they used. Refresh tokens are
 * single-use, so the parallel requests of one page (and an SSE reconnect) must share one
 * refresh; requests still carrying the old cookie for a minute get the same new session. This
 * is per BFF process: run one replica per session (sticky) or share the map if scaled out. */
const refreshes = new Map<string, { at: number; result: Promise<Session | null> }>();
const REFRESH_REUSE_MS = 60_000;

export function refreshSession(cfg: WebConfig, session: Session): Promise<Session | null> {
  const token = session.refreshToken;
  if (!token) return Promise.resolve(null);
  const now = Date.now();
  for (const [key, entry] of refreshes) {
    if (now - entry.at > REFRESH_REUSE_MS) refreshes.delete(key);
  }
  const existing = refreshes.get(token);
  if (existing) return existing.result;
  const result = tokenRequest(cfg, { grant_type: "refresh_token", refresh_token: token })
    .then((tokens) => sessionFromTokens(tokens, token))
    .catch(() => null);
  refreshes.set(token, { at: now, result });
  return result;
}
