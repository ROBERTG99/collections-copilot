"use client";

import type { ReactNode } from "react";

import { ApiError } from "@/lib/api/client";

/** Loading skeleton: dense placeholder rows announced to screen readers. */
export function Skeleton({ rows = 8, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" aria-label={label} className="space-y-1">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-8 animate-pulse rounded bg-skeleton" />
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  );
}

/** Empty state with the next action. */
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded border border-dashed border-rule p-6 text-center">
      <p className="font-semibold">{title}</p>
      {children ? <div className="mt-2 text-muted">{children}</div> : null}
    </div>
  );
}

/** Error state with the correlation id (for support) and a retry. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const problem = error instanceof ApiError ? error.problem : null;
  return (
    <div role="alert" className="rounded border border-danger p-4">
      <p className="font-semibold text-danger">
        {problem?.title ?? "Something went wrong"}
        {problem ? ` (${problem.status})` : ""}
      </p>
      <p className="mt-1">{problem?.detail ?? (error instanceof Error ? error.message : "")}</p>
      {problem?.correlation_id ? (
        <p className="mt-1 text-sm text-muted">
          Correlation ID: <span className="num">{problem.correlation_id}</span>
        </p>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded border border-rule px-3 py-1 hover:bg-surface-2"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}
