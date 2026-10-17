import { NextResponse, type NextRequest } from "next/server";

import { webConfig } from "@/lib/config";
import { clearSession } from "@/lib/server-session";

/** Sign out: same-origin form posts only (a cross-site page cannot sign the analyst out). */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const cfg = webConfig();
  const origin = request.headers.get("origin");
  if (origin && origin !== cfg.publicUrl) {
    return NextResponse.json({ title: "Forbidden", status: 403 }, { status: 403 });
  }
  await clearSession();
  const query = new URLSearchParams({ client_id: cfg.clientId, logout_uri: `${cfg.publicUrl}/` });
  return NextResponse.redirect(`${cfg.oidcPublicIssuer}/logout?${query.toString()}`, 303);
}
