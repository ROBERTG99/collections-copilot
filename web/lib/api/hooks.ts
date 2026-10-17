"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { AccountDetail, CaseDetail, QueuePage } from "@/lib/api/types";

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

export function useCase(caseId: string | null) {
  return useQuery({
    queryKey: ["case", caseId],
    enabled: !!caseId,
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
