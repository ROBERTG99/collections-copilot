import { describe, expect, it } from "vitest";

import { apiTarget, readCapped } from "@/lib/proxy";
import { webConfig } from "@/lib/config";

const BASE = "http://api:8310";

describe("BFF proxy target", () => {
  it("maps console paths under /api/v1", () => {
    expect(apiTarget(BASE, ["queue"], "?limit=20")).toBe("http://api:8310/api/v1/queue?limit=20");
    expect(apiTarget(BASE, ["cases", "abc", "approval"], "")).toBe(
      "http://api:8310/api/v1/cases/abc/approval",
    );
  });

  it.each([
    [["webhooks", "twilio", "inbound"]],
    [["Webhooks", "twilio"]],
    [["webhooks/twilio/inbound"]], // %2F decoded into one segment
    [[".", "webhooks"]],
    [[".."]],
    [["queue", ""]],
    [["a\\b"]],
    [[]],
  ])("refuses %j", (path) => {
    expect(apiTarget(BASE, path as string[], "")).toBeNull();
  });
});

describe("BFF body cap", () => {
  it("reads small bodies and refuses large ones without buffering them whole", async () => {
    const small = new Request("http://x", { method: "POST", body: "a".repeat(10) });
    expect((await readCapped(small, 100))?.byteLength).toBe(10);
    const big = new Request("http://x", { method: "POST", body: "a".repeat(200) });
    expect(await readCapped(big, 100)).toBeNull();
  });
});

describe("dev session secret", () => {
  it("is refused on a public URL even with the escape hatch", () => {
    expect(() =>
      webConfig({
        NODE_ENV: "production",
        WEB_PUBLIC_URL: "https://collections.example.com",
        WEB_SESSION_SECRET: "dev-only-secret-0123456789abcdef0123456789",
        WEB_ALLOW_DEV_SECRET: "true",
      } as unknown as NodeJS.ProcessEnv),
    ).toThrow(/WEB_SESSION_SECRET/);
    expect(() =>
      webConfig({
        NODE_ENV: "production",
        WEB_PUBLIC_URL: "http://localhost:8300",
        WEB_SESSION_SECRET: "dev-only-secret-0123456789abcdef0123456789",
        WEB_ALLOW_DEV_SECRET: "true",
      } as unknown as NodeJS.ProcessEnv),
    ).not.toThrow();
  });
});

describe("OIDC endpoints", () => {
  it("default to the dev-oidc layout under the issuers", () => {
    const cfg = webConfig({
      WEB_OIDC_ISSUER: "http://dev-oidc:8345",
      WEB_OIDC_PUBLIC_ISSUER: "http://localhost:8345",
    } as unknown as NodeJS.ProcessEnv);
    expect(cfg.endpoints).toEqual({
      authorize: "http://localhost:8345/authorize",
      token: "http://dev-oidc:8345/token",
      jwks: "http://dev-oidc:8345/.well-known/jwks.json",
      logout: "http://localhost:8345/logout",
    });
  });

  it("take Cognito's hosted-UI and issuer URLs when set", () => {
    const ui = "https://cc-dev.auth.us-east-1.amazoncognito.com";
    const issuer = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc";
    const cfg = webConfig({
      WEB_OIDC_ISSUER: issuer,
      WEB_OIDC_AUTHORIZE_URL: `${ui}/oauth2/authorize`,
      WEB_OIDC_TOKEN_URL: `${ui}/oauth2/token`,
      WEB_OIDC_LOGOUT_URL: `${ui}/logout`,
    } as unknown as NodeJS.ProcessEnv);
    expect(cfg.endpoints.authorize).toBe(`${ui}/oauth2/authorize`);
    expect(cfg.endpoints.token).toBe(`${ui}/oauth2/token`);
    expect(cfg.endpoints.jwks).toBe(`${issuer}/.well-known/jwks.json`);
    expect(cfg.endpoints.logout).toBe(`${ui}/logout`);
  });
});
