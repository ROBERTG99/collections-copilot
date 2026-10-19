import { NextResponse } from "next/server";

import { webConfig } from "@/lib/config";

export const dynamic = "force-dynamic";

/** Liveness for the container HEALTHCHECK and the ALB target group: the BFF is up and its
 * settings load (a production build refuses the dev session secret). */
export function GET(): NextResponse {
  const headers = { "cache-control": "no-store" };
  try {
    webConfig();
  } catch {
    return NextResponse.json({ status: "misconfigured" }, { status: 503, headers });
  }
  return NextResponse.json({ status: "ok" }, { headers });
}
