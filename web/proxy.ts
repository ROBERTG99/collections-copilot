import { NextResponse, type NextRequest } from "next/server";

/** Passes the requested console path to server components, so a sign-in or a session refresh
 * can return to the page the analyst asked for. */
export function proxy(request: NextRequest): NextResponse {
  const headers = new Headers(request.headers);
  headers.set("x-cc-path", `${request.nextUrl.pathname}${request.nextUrl.search}`);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: ["/((?!api/|auth/|_next/|healthz|favicon.ico).*)"],
};
