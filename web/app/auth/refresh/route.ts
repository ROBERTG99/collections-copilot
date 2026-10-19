import { NextResponse, type NextRequest } from "next/server";

import { webConfig } from "@/lib/config";
import { activeSession, clearSession } from "@/lib/server-session";

/** The console layout sends an expired session here: refresh it and go back, or sign in again. */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const cfg = webConfig();
  const requested = request.nextUrl.searchParams.get("returnTo") ?? "/queue";
  const returnTo = requested.startsWith("/") && !requested.startsWith("//") ? requested : "/queue";
  if (await activeSession()) return NextResponse.redirect(`${cfg.publicUrl}${returnTo}`);
  await clearSession();
  return NextResponse.redirect(
    `${cfg.publicUrl}/auth/login?returnTo=${encodeURIComponent(returnTo)}`,
  );
}
