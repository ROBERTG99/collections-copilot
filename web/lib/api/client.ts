/**
 * Typed API client over the generated OpenAPI types. Requests go to the BFF (`/api/...`), which
 * adds the bearer token server-side; `/api/v1/` paths in the schema are rewritten to `/api/`.
 */
import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "@/lib/api/schema";

export type Problem = {
  status: number;
  title?: string;
  detail?: string | null;
  code?: string;
  correlation_id?: string;
  [key: string]: unknown;
};

export class ApiError extends Error {
  constructor(readonly problem: Problem) {
    super(problem.detail || problem.title || `HTTP ${problem.status}`);
  }
}

const bff: Middleware = {
  async onRequest({ request }) {
    const url = new URL(request.url);
    url.pathname = url.pathname.replace(/^\/api\/v1\//, "/api/");
    const headers = new Headers(request.headers);
    headers.set("x-cc-csrf", "1");
    // Buffer the body: browsers refuse streamed request bodies over HTTP/1.1.
    const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.text();
    return new Request(url, {
      method: request.method,
      headers,
      body,
      credentials: "same-origin",
      signal: request.signal,
    });
  },
};

export const api = createClient<paths>({
  baseUrl: typeof window === "undefined" ? "http://localhost:8300" : window.location.origin,
});
api.use(bff);

/** Unwrap an openapi-fetch result or throw an ApiError carrying the problem document. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || result.data === undefined) {
    throw new ApiError(problemFrom(result.error, result.response));
  }
  return result.data;
}

/** A problem document from an error body; plain-text or empty bodies (a proxy, a crash) still
 * carry the status and the correlation id the BFF forwards in `x-correlation-id`. */
export function problemFrom(error: unknown, response: Response): Problem {
  const body =
    error !== null && typeof error === "object"
      ? (error as Partial<Problem>)
      : ({} as Partial<Problem>);
  return {
    ...body,
    title: body.title || response.statusText || undefined, // HTTP/2 has no reason phrase: ""
    detail: body.detail ?? (typeof error === "string" && error ? error.slice(0, 300) : undefined),
    correlation_id: body.correlation_id ?? response.headers.get("x-correlation-id") ?? undefined,
    status: response.status,
  };
}
