/**
 * BFF proxy: /api/<path> → API /api/v1/<path> with the session's bearer token added server-side.
 * The browser never sees a token. Mutating requests must be same-origin and carry the
 * `x-cc-csrf` header that only our fetch layer sets (a cross-site form cannot add headers).
 * Responses stream through unchanged, so SSE works.
 */
import { type NextRequest } from "next/server";

import { webConfig } from "@/lib/config";
import { activeSession } from "@/lib/server-session";
import { correlationId, traceparent } from "@/lib/trace";

const FORWARD_REQUEST = ["content-type", "accept", "idempotency-key", "last-event-id"];
const FORWARD_RESPONSE = [
  "content-type",
  "cache-control",
  "retry-after",
  "x-correlation-id",
  "idempotent-replayed",
  "x-ratelimit-limit",
  "x-ratelimit-remaining",
];
const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function problem(status: number, title: string, detail: string): Response {
  return Response.json(
    { type: "about:blank", title, status, detail, correlation_id: crypto.randomUUID() },
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const cfg = webConfig();
  if (path.length === 0 || path[0] === "webhooks" || path.some((p) => p === ".." || p === "")) {
    return problem(404, "Not Found", "no such API route");
  }
  if (MUTATING.has(request.method)) {
    const origin = request.headers.get("origin");
    if ((origin && origin !== cfg.publicUrl) || request.headers.get("x-cc-csrf") !== "1") {
      return problem(403, "Forbidden", "cross-site request refused");
    }
  }
  const session = await activeSession();
  if (!session) return problem(401, "Unauthorized", "sign in again");
  const headers = new Headers({
    authorization: `Bearer ${session.accessToken}`,
    "x-correlation-id": correlationId(request.headers.get("x-correlation-id")),
    traceparent: traceparent(),
  });
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const target = `${cfg.apiBaseUrl}/api/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: MUTATING.has(request.method) ? await request.arrayBuffer() : undefined,
    cache: "no-store",
    redirect: "manual",
    signal: request.signal,
  }).catch(() => null);
  if (!upstream) return problem(502, "Bad Gateway", "the API is unreachable");
  const out = new Headers();
  for (const name of FORWARD_RESPONSE) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  if (out.get("content-type")?.startsWith("text/event-stream")) out.set("x-accel-buffering", "no");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, ctx: Ctx): Promise<Response> {
  return proxy(request, (await ctx.params).path);
}
export async function POST(request: NextRequest, ctx: Ctx): Promise<Response> {
  return proxy(request, (await ctx.params).path);
}
export async function PUT(request: NextRequest, ctx: Ctx): Promise<Response> {
  return proxy(request, (await ctx.params).path);
}
export async function DELETE(request: NextRequest, ctx: Ctx): Promise<Response> {
  return proxy(request, (await ctx.params).path);
}

export const dynamic = "force-dynamic";
