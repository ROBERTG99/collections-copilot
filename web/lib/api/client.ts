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
    super(problem.detail ?? problem.title ?? `HTTP ${problem.status}`);
  }
}

const bff: Middleware = {
  onRequest({ request }) {
    const url = new URL(request.url);
    url.pathname = url.pathname.replace(/^\/api\/v1\//, "/api/");
    const headers = new Headers(request.headers);
    headers.set("x-cc-csrf", "1");
    return new Request(url, { ...requestInit(request), headers });
  },
};

function requestInit(request: Request): RequestInit {
  return {
    method: request.method,
    body: request.body,
    credentials: "same-origin",
    signal: request.signal,
    // @ts-expect-error - required by undici/browsers when streaming a body
    duplex: "half",
  };
}

export const api = createClient<paths>({
  baseUrl: typeof window === "undefined" ? "http://localhost:8300" : window.location.origin,
});
api.use(bff);

/** Unwrap an openapi-fetch result or throw an ApiError carrying the problem document. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || result.data === undefined) {
    const body = (result.error ?? {}) as Partial<Problem>;
    throw new ApiError({ ...body, status: result.response.status });
  }
  return result.data;
}
