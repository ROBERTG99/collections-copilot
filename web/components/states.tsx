"use client";

import type { ReactNode } from "react";

import { ApiError } from "@/lib/api/client";
import { humanize } from "@/lib/format";

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

/** Empty state with the next action (`action`: a button or link). */
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="rounded border border-dashed border-rule p-6 text-center">
      <p className="font-semibold">{title}</p>
      {children ? <div className="mt-2 text-muted">{children}</div> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

type Violation = { code?: string; message?: string; blocking?: boolean };

/** Error state with the correlation id (for support) and a retry; a 401 offers sign-in instead,
 * and a refused post-check lists its violations. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const problem = error instanceof ApiError ? error.problem : null;
  const postcheck = (problem?.postcheck ?? null) as { violations?: Violation[] } | null;
  const violations = (postcheck?.violations ?? []).filter((v) => v.blocking !== false);
  const signedOut = problem?.status === 401;
  return (
    <div role="alert" className="rounded border border-danger p-4">
      <p className="font-semibold text-danger">
        {signedOut ? "Your session has ended" : (problem?.title ?? "Something went wrong")}
        {problem ? ` (${problem.status})` : ""}
      </p>
      <p className="mt-1">{problem?.detail ?? (error instanceof Error ? error.message : "")}</p>
      {violations.length ? (
        <ul className="mt-2 list-disc pl-5 text-sm" data-testid="problem-violations">
          {violations.map((v, i) => (
            <li key={`${v.code}-${i}`}>
              <span className="font-semibold">{humanize(v.code)}</span>: {v.message}
            </li>
          ))}
        </ul>
      ) : null}
      {problem?.correlation_id ? (
        <p className="mt-1 text-sm text-muted">
          Correlation ID: <span className="num">{problem.correlation_id}</span>
        </p>
      ) : null}
      {signedOut ? (
        <a
          href={`/auth/login?returnTo=${encodeURIComponent(currentPath())}`}
          className="mt-3 inline-block rounded border border-rule px-3 py-1 hover:bg-surface-2"
        >
          Sign in again
        </a>
      ) : onRetry ? (
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

function currentPath(): string {
  return typeof window === "undefined"
    ? "/queue"
    : window.location.pathname + window.location.search;
}
