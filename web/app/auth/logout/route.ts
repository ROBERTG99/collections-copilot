import { NextResponse } from "next/server";

import { webConfig } from "@/lib/config";
import { clearSession } from "@/lib/server-session";

export async function POST(): Promise<NextResponse> {
  const cfg = webConfig();
  await clearSession();
  const query = new URLSearchParams({ client_id: cfg.clientId, logout_uri: `${cfg.publicUrl}/` });
  return NextResponse.redirect(`${cfg.oidcPublicIssuer}/logout?${query.toString()}`, 303);
}
