import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

// openapi-fetch captures `globalThis.fetch` when the client is created (at import time).
const net = vi.hoisted(() => {
  const original = globalThis.fetch;
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>();
  globalThis.fetch = ((input: Request) => fetchMock(input)) as typeof fetch;
  return { original, fetchMock };
});

import { api, ApiError, problemFrom, unwrap } from "@/lib/api/client";

afterAll(() => {
  globalThis.fetch = net.original;
});

beforeEach(() => {
  net.fetchMock.mockReset();
});

function lastRequest(): Request {
  const call = net.fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("no request was made");
  return call[0];
}

describe("api client through the BFF", () => {
  it("rewrites /api/v1/ to the BFF's /api/ and marks the request with the CSRF header", async () => {
    net.fetchMock.mockResolvedValue(Response.json({ items: [], next_cursor: null }));
    const result = await api.GET("/api/v1/queue", { params: { query: { limit: 50, q: "acme" } } });
    expect(result.data).toEqual({ items: [], next_cursor: null });
    const request = lastRequest();
    expect(request.method).toBe("GET");
    expect(request.url).toBe("http://localhost:8300/api/queue?limit=50&q=acme");
    expect(request.headers.get("x-cc-csrf")).toBe("1");
    expect(request.credentials).toBe("same-origin");
  });

  it("buffers the JSON body of a POST and keeps caller headers", async () => {
    net.fetchMock.mockResolvedValue(Response.json({ ok: true }));
    await api.POST("/api/v1/cases/{case_id}/approval", {
      params: { path: { case_id: "case-1" } },
      body: { decision: "approve", draft_version: 2 },
      headers: { "Idempotency-Key": "case-1:2:approve:811c9dc5" },
    });
    const request = lastRequest();
    expect(request.method).toBe("POST");
    expect(request.url).toBe("http://localhost:8300/api/cases/case-1/approval");
    expect(request.headers.get("Idempotency-Key")).toBe("case-1:2:approve:811c9dc5");
    expect(request.headers.get("x-cc-csrf")).toBe("1");
    expect(await request.json()).toEqual({ decision: "approve", draft_version: 2 });
  });

  it("throws an ApiError with the problem document via unwrap", async () => {
    net.fetchMock.mockResolvedValue(
      Response.json(
        { status: 409, title: "Conflict", detail: "Draft v3 replaced v2.", correlation_id: "c-1" },
        { status: 409, headers: { "content-type": "application/problem+json" } },
      ),
    );
    const result = await api.GET("/api/v1/cases/{case_id}", {
      params: { path: { case_id: "case-1" } },
    });
    let thrown: unknown;
    try {
      unwrap(result);
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(ApiError);
    const err = thrown as ApiError;
    expect(err.message).toBe("Draft v3 replaced v2.");
    expect(err.problem).toMatchObject({
      status: 409,
      title: "Conflict",
      correlation_id: "c-1",
    });
  });
});

describe("unwrap", () => {
  const ok = new Response(null, { status: 200 });

  it("returns the data of a successful result", () => {
    expect(unwrap({ data: { id: 1 }, response: ok })).toEqual({ id: 1 });
  });

  it("treats a missing body as an error", () => {
    expect(() =>
      unwrap({ data: undefined, response: new Response(null, { status: 204 }) }),
    ).toThrow(ApiError);
  });
});

describe("problemFrom", () => {
  it("keeps the problem document and the status of the response", () => {
    const response = new Response(null, { status: 422, statusText: "Unprocessable Entity" });
    expect(
      problemFrom(
        { title: "Post-check failed", detail: "quiet hours", code: "postcheck" },
        response,
      ),
    ).toEqual({
      title: "Post-check failed",
      detail: "quiet hours",
      code: "postcheck",
      correlation_id: undefined,
      status: 422,
    });
  });

  it("uses the status text and forwarded correlation id for a plain-text body", () => {
    const response = new Response(null, {
      status: 502,
      statusText: "Bad Gateway",
      headers: { "x-correlation-id": "corr-9" },
    });
    const problem = problemFrom("upstream connect error ".repeat(40), response);
    expect(problem.status).toBe(502);
    expect(problem.title).toBe("Bad Gateway");
    expect(problem.correlation_id).toBe("corr-9");
    expect(problem.detail).toHaveLength(300); // truncated
    expect(problem.detail?.startsWith("upstream connect error")).toBe(true);
  });

  it("still carries the status for an empty body", () => {
    const problem = problemFrom("", new Response(null, { status: 500 }));
    expect(problem.status).toBe(500);
    expect(problem.detail).toBeUndefined();
    expect(problem.correlation_id).toBeUndefined();
    // No reason phrase (always so over HTTP/2): no empty title, so the UI falls back to words.
    expect(problem.title).toBeUndefined();
    expect(new ApiError(problem).message).toBe("HTTP 500");
  });

  it("prefers the body's correlation id over the header", () => {
    const response = new Response(null, {
      status: 400,
      headers: { "x-correlation-id": "from-header" },
    });
    expect(problemFrom({ correlation_id: "from-body" }, response).correlation_id).toBe("from-body");
    expect(problemFrom(null, response).correlation_id).toBe("from-header");
  });
});

describe("ApiError", () => {
  it("uses detail, then title, then the status as its message", () => {
    expect(new ApiError({ status: 400, title: "Bad Request", detail: "bad phone" }).message).toBe(
      "bad phone",
    );
    expect(new ApiError({ status: 400, title: "Bad Request" }).message).toBe("Bad Request");
    expect(new ApiError({ status: 503 }).message).toBe("HTTP 503");
  });
});
