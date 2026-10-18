// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  type MockInstance,
  vi,
} from "vitest";

// openapi-fetch captures `globalThis.fetch` when the client is created (at import time).
const net = vi.hoisted(() => {
  const original = globalThis.fetch;
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>();
  globalThis.fetch = ((input: Request) => fetchMock(input)) as typeof fetch;
  return { original, fetchMock };
});

import { ApiError } from "@/lib/api/client";
import {
  decisionKey,
  postcheck,
  useAccount,
  useAddOptOut,
  useAudit,
  useAutoApproval,
  useBatchApprove,
  useCase,
  useDecision,
  useFollowUps,
  useOps,
  useOptOuts,
  usePromiseDecision,
  usePromises,
  useQueue,
  useReclassify,
  useReplies,
  useSaveAutoApproval,
  useStartCases,
} from "@/lib/api/hooks";

afterAll(() => {
  globalThis.fetch = net.original;
});

let client: QueryClient;
let invalidate: MockInstance<QueryClient["invalidateQueries"]>;

beforeEach(() => {
  net.fetchMock.mockReset();
  net.fetchMock.mockImplementation(async () => Response.json({ ok: true }));
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  invalidate = vi.spyOn(client, "invalidateQueries");
});

afterEach(() => {
  cleanup();
  client.clear();
});

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

type Seen = { method: string; path: string; query: Record<string, string>; request: Request };

function requests(): Seen[] {
  return net.fetchMock.mock.calls.map(([request]) => {
    const url = new URL(request.url);
    return {
      method: request.method,
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      request,
    };
  });
}

function only(): Seen {
  const all = requests();
  expect(all).toHaveLength(1);
  return all[0] as Seen;
}

function invalidatedKeys(): unknown[] {
  return invalidate.mock.calls.map((c) => c[0]?.queryKey);
}

describe("queries", () => {
  it("useQueue pages through the queue with the filters and the cursor", async () => {
    net.fetchMock
      .mockResolvedValueOnce(Response.json({ items: [{ account_id: "a1" }], next_cursor: "c2" }))
      .mockResolvedValueOnce(Response.json({ items: [{ account_id: "a2" }], next_cursor: null }));
    const { result } = renderHook(() => useQueue({ segment: "smb", min_score: 40 }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);
    await act(() => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
    expect(result.current.hasNextPage).toBe(false);

    const [first, second] = requests();
    expect(first?.method).toBe("GET");
    expect(first?.path).toBe("/api/queue"); // the BFF path, not /api/v1/
    expect(first?.query).toEqual({ limit: "50", segment: "smb", min_score: "40" });
    expect(second?.query).toEqual({ limit: "50", cursor: "c2", segment: "smb", min_score: "40" });
    expect(first?.request.headers.get("x-cc-csrf")).toBe("1");
  });

  it("useAccount loads one account", async () => {
    net.fetchMock.mockResolvedValue(Response.json({ account_id: "acc-1" }));
    const { result } = renderHook(() => useAccount("acc-1"), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual({ account_id: "acc-1" }));
    expect(only().path).toBe("/api/accounts/acc-1");
  });

  it("useCase waits for a case id and surfaces problem documents as ApiError", async () => {
    const { result, rerender } = renderHook(({ id }: { id: string | null }) => useCase(id), {
      wrapper,
      initialProps: { id: null as string | null },
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(net.fetchMock).not.toHaveBeenCalled();

    net.fetchMock.mockResolvedValue(
      Response.json(
        { title: "Not Found", detail: "no such case", correlation_id: "c-404" },
        { status: 404 },
      ),
    );
    rerender({ id: "case-9" });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(only().path).toBe("/api/cases/case-9");
    const error = result.current.error;
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).problem).toMatchObject({ status: 404, correlation_id: "c-404" });
  });

  const reads: [string, () => { isSuccess: boolean }, string, Record<string, string>][] = [
    ["useReplies", () => useReplies(), "/api/replies", { limit: "200" }],
    ["usePromises", () => usePromises(), "/api/promises", { limit: "200" }],
    ["useFollowUps", () => useFollowUps(), "/api/follow-ups", { status: "pending", limit: "200" }],
    ["useOps", () => useOps(7), "/api/metrics/ops", { window_days: "7" }],
    ["useAutoApproval", () => useAutoApproval(), "/api/settings/auto-approval", {}],
    ["useOptOuts", () => useOptOuts(), "/api/opt-outs", { limit: "200" }],
    [
      "useAudit with an action",
      () => useAudit("sms.sent"),
      "/api/audit",
      { limit: "200", verify: "true", action: "sms.sent" },
    ],
    [
      "useAudit for every action",
      () => useAudit(""),
      "/api/audit",
      { limit: "200", verify: "true" },
    ],
  ];
  it.each(reads)("%s requests %s", async (_name, hook, path, query) => {
    const { result } = renderHook(hook, { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const seen = only();
    expect(seen.method).toBe("GET");
    expect(seen.path).toBe(path);
    expect(seen.query).toEqual(query);
  });
});

describe("postcheck", () => {
  it("posts the exact text and returns the check result", async () => {
    const check = { ok: true, deferred: false, send_at: "2026-10-19T12:00:00Z", violations: [] };
    net.fetchMock.mockResolvedValue(Response.json(check));
    expect(await postcheck("case-1", "Hi Ana")).toEqual(check);
    const seen = only();
    expect(seen.method).toBe("POST");
    expect(seen.path).toBe("/api/cases/case-1/postcheck");
    expect(await seen.request.json()).toEqual({ body: "Hi Ana" });
  });

  it("throws an ApiError when the check is refused", async () => {
    net.fetchMock.mockResolvedValue(Response.json({ title: "Forbidden" }, { status: 403 }));
    await expect(postcheck("case-1", "x")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("mutations", () => {
  it("useDecision posts the decision with its idempotency key and refreshes the case and queue", async () => {
    const { result } = renderHook(() => useDecision("case-1"), { wrapper });
    const decision = { decision: "edit" as const, draft_version: 2, body: "Hi Ana, thanks." };
    await act(() => result.current.mutateAsync(decision));
    const seen = only();
    expect(seen.method).toBe("POST");
    expect(seen.path).toBe("/api/cases/case-1/approval");
    expect(seen.request.headers.get("Idempotency-Key")).toBe(decisionKey("case-1", decision));
    expect(seen.request.headers.get("x-cc-csrf")).toBe("1");
    expect(await seen.request.json()).toEqual(decision);
    expect(invalidatedKeys()).toEqual([["case", "case-1"], ["queue"]]);
  });

  it("useDecision does not refresh anything when the API refuses", async () => {
    net.fetchMock.mockResolvedValue(Response.json({ title: "Conflict" }, { status: 409 }));
    const { result } = renderHook(() => useDecision("case-1"), { wrapper });
    act(() => result.current.mutate({ decision: "approve", draft_version: 1 }));
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("useStartCases starts cases and refreshes the queue and accounts", async () => {
    const { result } = renderHook(() => useStartCases(), { wrapper });
    await act(() => result.current.mutateAsync(["a1", "a2"]));
    const seen = only();
    expect([seen.method, seen.path]).toEqual(["POST", "/api/cases"]);
    expect(await seen.request.json()).toEqual({ account_ids: ["a1", "a2"] });
    expect(invalidatedKeys()).toEqual([["queue"], ["account"]]);
  });

  it("useBatchApprove approves every item under one stable idempotency key", async () => {
    const items = [
      { case_id: "c1", draft_version: 1 },
      { case_id: "c2", draft_version: 3 },
    ];
    const { result } = renderHook(() => useBatchApprove(), { wrapper });
    await act(() => result.current.mutateAsync(items));
    await act(() => result.current.mutateAsync(items));
    await act(() => result.current.mutateAsync([items[0] as (typeof items)[number]]));
    const [first, again, other] = requests();
    expect(first?.path).toBe("/api/approvals:batch");
    expect(await first?.request.json()).toEqual({
      items: [
        { case_id: "c1", draft_version: 1, decision: "approve" },
        { case_id: "c2", draft_version: 3, decision: "approve" },
      ],
    });
    const key = first?.request.headers.get("Idempotency-Key");
    expect(key).toMatch(/^batch:[0-9a-f]{8}$/);
    expect(again?.request.headers.get("Idempotency-Key")).toBe(key);
    expect(other?.request.headers.get("Idempotency-Key")).not.toBe(key);
    expect(invalidatedKeys().slice(0, 2)).toEqual([["queue"], ["case"]]);
  });

  it("useReclassify relabels a reply and refreshes the replies", async () => {
    const { result } = renderHook(() => useReclassify(), { wrapper });
    await act(() => result.current.mutateAsync({ id: "m-1", label: "dispute" }));
    const seen = only();
    expect([seen.method, seen.path]).toEqual(["POST", "/api/replies/m-1:reclassify"]);
    expect(await seen.request.json()).toEqual({ label: "dispute" });
    expect(invalidatedKeys()).toEqual([["replies"]]);
  });

  it("usePromiseDecision approves without a body and rejects with the reason", async () => {
    const { result } = renderHook(() => usePromiseDecision(), { wrapper });
    await act(() => result.current.mutateAsync({ id: "p-1", approve: true }));
    await act(() =>
      result.current.mutateAsync({ id: "p-2", approve: false, reason: "amount mismatch" }),
    );
    await act(() => result.current.mutateAsync({ id: "p-3", approve: false }));
    const [approve, reject, bare] = requests();
    expect([approve?.method, approve?.path]).toEqual(["POST", "/api/promises/p-1:approve"]);
    expect(await approve?.request.text()).toBe("");
    expect(reject?.path).toBe("/api/promises/p-2:reject");
    expect(await reject?.request.json()).toEqual({ reason: "amount mismatch" });
    expect(await bare?.request.json()).toEqual({ reason: "" });
    expect(invalidatedKeys()).toEqual([["promises"], ["promises"], ["promises"]]);
  });

  it("useSaveAutoApproval PUTs the policy and refreshes it", async () => {
    const policy = { enabled: true, max_usd: 500, min_confidence: 0.9 };
    const { result } = renderHook(() => useSaveAutoApproval(), { wrapper });
    await act(() => result.current.mutateAsync(policy));
    const seen = only();
    expect([seen.method, seen.path]).toEqual(["PUT", "/api/settings/auto-approval"]);
    expect(await seen.request.json()).toEqual(policy);
    expect(invalidatedKeys()).toEqual([["auto-approval"]]);
  });

  it("useAddOptOut records the phone number and refreshes the list", async () => {
    const { result } = renderHook(() => useAddOptOut(), { wrapper });
    await act(() => result.current.mutateAsync("+15555550123"));
    const seen = only();
    expect([seen.method, seen.path]).toEqual(["POST", "/api/opt-outs"]);
    expect(await seen.request.json()).toEqual({ phone_e164: "+15555550123" });
    expect(invalidatedKeys()).toEqual([["opt-outs"]]);
  });
});
