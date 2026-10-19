/** Server-only settings for the BFF (documented in the root .env.example under WEB_*). */
export type WebConfig = {
  sessionSecret: string;
  apiBaseUrl: string;
  oidcIssuer: string; // reachable from the BFF (token endpoint)
  oidcPublicIssuer: string; // reachable from the browser (authorize, logout)
  clientId: string;
  publicUrl: string;
  secureCookies: boolean;
  /** dev-oidc serves them under its issuers; Cognito's hosted UI uses another host. */
  endpoints: { authorize: string; token: string; jwks: string; logout: string };
};

export function webConfig(env: NodeJS.ProcessEnv = process.env): WebConfig {
  const publicUrl = env.WEB_PUBLIC_URL ?? "http://localhost:8300";
  const secret = env.WEB_SESSION_SECRET ?? "";
  // The escape hatch (the compose stack runs a production build) never applies to a public URL.
  const local = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/.test(publicUrl);
  if (
    env.NODE_ENV === "production" &&
    secret.startsWith("dev-only") &&
    (env.WEB_ALLOW_DEV_SECRET !== "true" || !local)
  ) {
    throw new Error("WEB_SESSION_SECRET must be set to a real secret in production");
  }
  const strip = (url: string) => url.replace(/\/$/, "");
  const oidcIssuer = strip(env.WEB_OIDC_ISSUER ?? "http://localhost:8345");
  const oidcPublicIssuer = strip(
    env.WEB_OIDC_PUBLIC_ISSUER ?? env.WEB_OIDC_ISSUER ?? "http://localhost:8345",
  );
  return {
    endpoints: {
      authorize: env.WEB_OIDC_AUTHORIZE_URL ?? `${oidcPublicIssuer}/authorize`,
      token: env.WEB_OIDC_TOKEN_URL ?? `${oidcIssuer}/token`,
      jwks: env.WEB_OIDC_JWKS_URL ?? `${oidcIssuer}/.well-known/jwks.json`,
      logout: env.WEB_OIDC_LOGOUT_URL ?? `${oidcPublicIssuer}/logout`,
    },
    sessionSecret: secret,
    apiBaseUrl: (env.WEB_API_BASE_URL ?? "http://localhost:8310").replace(/\/$/, ""),
    oidcIssuer: (env.WEB_OIDC_ISSUER ?? "http://localhost:8345").replace(/\/$/, ""),
    oidcPublicIssuer: (
      env.WEB_OIDC_PUBLIC_ISSUER ??
      env.WEB_OIDC_ISSUER ??
      "http://localhost:8345"
    ).replace(/\/$/, ""),
    clientId: env.WEB_OIDC_CLIENT_ID ?? "cc-web",
    publicUrl: publicUrl.replace(/\/$/, ""),
    secureCookies: publicUrl.startsWith("https://"),
  };
}
