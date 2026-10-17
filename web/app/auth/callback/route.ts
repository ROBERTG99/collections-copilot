import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { webConfig } from "@/lib/config";
import { exchangeCode } from "@/lib/oidc";
import { writeSession } from "@/lib/server-session";
import { OIDC_COOKIE, openOidcState } from "@/lib/session";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const cfg = webConfig();
  const jar = await cookies();
  const saved = await openOidcState(jar.get(OIDC_COOKIE)?.value, cfg.sessionSecret);
  jar.delete({ name: OIDC_COOKIE, path: "/auth" }); // it was set on /auth
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  if (!saved || !code || !state || state !== saved.state) {
    return NextResponse.redirect(`${cfg.publicUrl}/?error=signin_failed`);
  }
  try {
    await writeSession(await exchangeCode(cfg, code, saved.verifier, saved.nonce));
  } catch {
    return NextResponse.redirect(`${cfg.publicUrl}/?error=signin_failed`);
  }
  return NextResponse.redirect(`${cfg.publicUrl}${saved.returnTo}`);
}
