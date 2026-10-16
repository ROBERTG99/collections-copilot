/** Server-only settings for the BFF (documented in the root .env.example under WEB_*). */
export type WebConfig = {
  sessionSecret: string;
  apiBaseUrl: string;
  oidcIssuer: string; // reachable from the BFF (token endpoint)
  oidcPublicIssuer: string; // reachable from the browser (authorize, logout)
  clientId: string;
  publicUrl: string;
  secureCookies: boolean;
};

export function webConfig(env: NodeJS.ProcessEnv = process.env): WebConfig {
  const publicUrl = env.WEB_PUBLIC_URL ?? "http://localhost:8300";
  const secret = env.WEB_SESSION_SECRET ?? "";
  if (env.NODE_ENV === "production" && secret.startsWith("dev-only") && env.WEB_ALLOW_DEV_SECRET !== "true") {
    throw new Error("WEB_SESSION_SECRET must be set to a real secret in production");
  }
  return {
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
