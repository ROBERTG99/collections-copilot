import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { webConfig } from "@/lib/config";
import { authorizeUrl, pkceChallenge, randomToken } from "@/lib/oidc";
import { OIDC_COOKIE, sealOidcState } from "@/lib/session";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const cfg = webConfig();
  const requested = request.nextUrl.searchParams.get("returnTo") ?? "/queue";
  // Only local paths: never redirect off-site after sign-in.
  const returnTo = requested.startsWith("/") && !requested.startsWith("//") ? requested : "/queue";
  const state = randomToken();
  const verifier = randomToken(48);
  const nonce = randomToken();
  const jar = await cookies();
  jar.set(
    OIDC_COOKIE,
    await sealOidcState({ state, verifier, nonce, returnTo }, cfg.sessionSecret),
    {
      httpOnly: true,
      secure: cfg.secureCookies,
      sameSite: "lax",
      path: "/auth",
      maxAge: 600,
    },
  );
  return NextResponse.redirect(
    authorizeUrl(cfg, { state, challenge: await pkceChallenge(verifier), nonce }),
  );
}
