"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { AccountDetail, CaseDetail, CheckOut, QueuePage } from "@/lib/api/types";

export type QueueFilters = {
  segment?: "enterprise" | "mid_market" | "smb";
  min_score?: number;
  status?: "no_case" | "awaiting_approval" | "queued" | "awaiting_reply" | "requires_analyst";
  q?: string;
};

export function useQueue(filters: QueueFilters) {
  return useInfiniteQuery({
    queryKey: ["queue", filters],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/queue", {
          params: { query: { limit: 50, cursor: pageParam, ...filters } },
        }),
      ) as QueuePage,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

export function useAccount(accountId: string) {
  return useQuery({
    queryKey: ["account", accountId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/accounts/{account_id}", {
          params: { path: { account_id: accountId } },
        }),
      ) as AccountDetail,
  });
}

export function useCase(caseId: string | null, refetchInterval: number | false = false) {
  return useQuery({
    queryKey: ["case", caseId],
    enabled: !!caseId,
    refetchInterval,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/cases/{case_id}", {
          params: { path: { case_id: caseId ?? "" } },
        }),
      ) as CaseDetail,
  });
}

export function useStartCases() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (accountIds: string[]) =>
      unwrap(await api.POST("/api/v1/cases", { body: { account_ids: accountIds } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["queue"] });
      void client.invalidateQueries({ queryKey: ["account"] });
    },
  });
}

export type CheckResult = CheckOut;

export async function postcheck(caseId: string, body: string): Promise<CheckResult> {
  return unwrap(
    await api.POST("/api/v1/cases/{case_id}/postcheck", {
      params: { path: { case_id: caseId } },
      body: { body },
    }),
  );
}

export type Decision =
  | { decision: "approve"; draft_version: number }
  | { decision: "edit"; draft_version: number; body: string }
  | { decision: "reject"; draft_version: number; reason: string };

/**
 * One key per distinct request: a network retry of the same decision replays the stored result,
 * while a corrected edit (different body) or a new reason is a new request. A key without the
 * body locked the edit path for 24 h after one refused edit (M4 review).
 */
export function decisionKey(caseId: string, decision: Decision): string {
  const content =
    decision.decision === "edit"
      ? decision.body
      : decision.decision === "reject"
        ? decision.reason
        : "";
  return `${caseId}:${decision.draft_version}:${decision.decision}:${fnv1a(content)}`;
}

function fnv1a(text: string): string {
  let hash = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(text)) {
    hash ^= byte;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

export function useDecision(caseId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (decision: Decision) =>
      unwrap(
        await api.POST("/api/v1/cases/{case_id}/approval", {
          params: { path: { case_id: caseId } },
          body: decision,
          headers: { "Idempotency-Key": decisionKey(caseId, decision) },
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["case", caseId] });
      void client.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}

export const MAX_BATCH = 50;

/** FR-6 batch approval (≤ 50): each item is decided on its own and reported back. */
export function useBatchApprove() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (items: { case_id: string; draft_version: number }[]) =>
      unwrap(
        await api.POST("/api/v1/approvals:batch", {
          body: { items: items.map((i) => ({ ...i, decision: "approve" as const })) },
          headers: {
            "Idempotency-Key": `batch:${fnv1a(items.map((i) => `${i.case_id}:${i.draft_version}`).join(","))}`,
          },
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["queue"] });
      void client.invalidateQueries({ queryKey: ["case"] });
    },
  });
}

export function useReplies() {
  return useQuery({
    queryKey: ["replies"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/replies", { params: { query: { limit: 200 } } })),
  });
}

export function useReclassify() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (v: { id: string; label: Reclassify }) =>
      unwrap(
        await api.POST("/api/v1/replies/{message_id}:reclassify", {
          params: { path: { message_id: v.id } },
          body: { label: v.label },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["replies"] }),
  });
}
export type Reclassify = "promise_to_pay" | "dispute" | "wrong_number" | "paid" | "other";

export function usePromises() {
  return useQuery({
    queryKey: ["promises"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/promises", { params: { query: { limit: 200 } } })),
  });
}

export function useFollowUps() {
  return useQuery({
    queryKey: ["follow-ups"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/follow-ups", {
          params: { query: { status: "pending", limit: 200 } },
        }),
      ),
  });
}

export function usePromiseDecision() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (v: { id: string; approve: boolean; reason?: string }) =>
      v.approve
        ? unwrap(
            await api.POST("/api/v1/promises/{promise_id}:approve", {
              params: { path: { promise_id: v.id } },
            }),
          )
        : unwrap(
            await api.POST("/api/v1/promises/{promise_id}:reject", {
              params: { path: { promise_id: v.id } },
              body: { reason: v.reason ?? "" },
            }),
          ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["promises"] }),
  });
}

export function useAudit(action: string | undefined) {
  return useQuery({
    queryKey: ["audit", action],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/audit", {
          params: { query: { limit: 200, verify: true, action: action || undefined } },
        }),
      ),
  });
}

export function useOps(windowDays: number) {
  return useQuery({
    queryKey: ["ops", windowDays],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/metrics/ops", { params: { query: { window_days: windowDays } } }),
      ),
  });
}

export function useAutoApproval() {
  return useQuery({
    queryKey: ["auto-approval"],
    queryFn: async () => unwrap(await api.GET("/api/v1/settings/auto-approval")),
  });
}

export function useSaveAutoApproval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (v: { enabled: boolean; max_usd: number; min_confidence: number }) =>
      unwrap(await api.PUT("/api/v1/settings/auto-approval", { body: v })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["auto-approval"] }),
  });
}

export function useOptOuts() {
  return useQuery({
    queryKey: ["opt-outs"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/opt-outs", { params: { query: { limit: 200 } } })),
  });
}

export function useAddOptOut() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (phone: string) =>
      unwrap(await api.POST("/api/v1/opt-outs", { body: { phone_e164: phone } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["opt-outs"] }),
  });
}
