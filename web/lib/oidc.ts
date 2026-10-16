/** Authorization code + PKCE (S256) against Cognito or dev-oidc. Server-side only. */
import { decodeJwt } from "jose";

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

export async function exchangeCode(
  cfg: WebConfig,
  code: string,
  verifier: string,
): Promise<Session> {
  const tokens = await tokenRequest(cfg, {
    grant_type: "authorization_code",
    code,
    code_verifier: verifier,
    redirect_uri: `${cfg.publicUrl}/auth/callback`,
  });
  return sessionFromTokens(tokens);
}

export async function refreshSession(cfg: WebConfig, session: Session): Promise<Session | null> {
  if (!session.refreshToken) return null;
  try {
    const tokens = await tokenRequest(cfg, {
      grant_type: "refresh_token",
      refresh_token: session.refreshToken,
    });
    return sessionFromTokens(tokens, session.refreshToken);
  } catch {
    return null;
  }
}
