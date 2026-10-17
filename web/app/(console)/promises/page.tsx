"use client";

import { useState } from "react";

import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useHasRole } from "@/components/user-context";
import { useFollowUps, usePromiseDecision, usePromises } from "@/lib/api/hooks";
import { formatDate, formatDateTime, formatMoney, humanize } from "@/lib/format";

export default function PromisesPage() {
  const promises = usePromises();
  const followUps = useFollowUps();
  const decide = usePromiseDecision();
  const supervisor = useHasRole("supervisor", "admin");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  return (
    <div className="space-y-8">
      <section aria-labelledby="promises-title">
        <h1 id="promises-title" className="text-xl font-semibold">
          Promises to pay
        </h1>
        <p className="text-sm text-muted">
          Written to the ledger through mcp-gateway; large or distant promises wait for a
          supervisor.
        </p>
        <div className="mt-4">
          {promises.isPending ? (
            <Skeleton label="Loading promises" />
          ) : promises.isError ? (
            <ErrorState error={promises.error} onRetry={() => void promises.refetch()} />
          ) : promises.data.length === 0 ? (
            <Empty title="No promises yet">They appear when a debtor promises to pay by SMS.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="promises">
                <caption className="sr-only">Promises to pay, newest first</caption>
                <thead>
                  <tr className="border-b border-rule text-left">
                    <th scope="col" className="text-right">
                      Amount
                    </th>
                    <th scope="col">Promised for</th>
                    <th scope="col">Status</th>
                    <th scope="col">Gateway approval</th>
                    <th scope="col">Ledger</th>
                    <th scope="col">Decision</th>
                  </tr>
                </thead>
                <tbody>
                  {promises.data.map((p) => (
                    <tr key={p.id} className="h-8 border-b border-rule">
                      <td className="num text-right">{formatMoney(p.amount_cents, p.currency)}</td>
                      <td>{formatDate(p.promised_date)}</td>
                      <td>{humanize(p.status)}</td>
                      <td className="num text-xs">{p.mcp_approval_id ?? "—"}</td>
                      <td className="num text-xs">{p.ledger_ref ?? p.failure_reason ?? "—"}</td>
                      <td>
                        {p.status === "approval_required" && supervisor ? (
                          <span className="flex flex-wrap items-center gap-1">
                            <button
                              type="button"
                              disabled={decide.isPending}
                              onClick={() => decide.mutate({ id: p.id, approve: true })}
                              className="rounded bg-teal px-2 py-0.5 text-teal-ink"
                            >
                              Approve
                            </button>
                            <label className="sr-only" htmlFor={`reason-${p.id}`}>
                              Reason to reject
                            </label>
                            <input
                              id={`reason-${p.id}`}
                              placeholder="Reason"
                              value={reasons[p.id] ?? ""}
                              onChange={(e) =>
                                setReasons((r) => ({ ...r, [p.id]: e.target.value }))
                              }
                              className="w-28 rounded border border-rule bg-surface px-1"
                            />
                            <button
                              type="button"
                              disabled={!reasons[p.id]?.trim() || decide.isPending}
                              onClick={() =>
                                decide.mutate({ id: p.id, approve: false, reason: reasons[p.id] })
                              }
                              className="rounded border border-danger px-2 py-0.5 text-danger disabled:opacity-50"
                            >
                              Reject
                            </button>
                          </span>
                        ) : p.status === "approval_required" ? (
                          <span className="text-muted">Waiting for a supervisor</span>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {decide.isError ? <ErrorState error={decide.error} /> : null}
          {decide.isSuccess ? (
            <p role="status" className="mt-2 text-sm text-teal">
              Decision sent to the gateway.
            </p>
          ) : null}
        </div>
      </section>

      <section aria-labelledby="followups-title">
        <h2 id="followups-title" className="text-lg font-semibold">
          Upcoming follow-ups
        </h2>
        {followUps.isPending ? (
          <Skeleton rows={4} label="Loading follow-ups" />
        ) : followUps.isError ? (
          <ErrorState error={followUps.error} onRetry={() => void followUps.refetch()} />
        ) : followUps.data.length === 0 ? (
          <Empty title="Nothing scheduled" />
        ) : (
          <ul className="mt-2 space-y-1 text-sm">
            {followUps.data.map((f) => (
              <li key={f.id}>
                {humanize(f.kind)} · due {formatDateTime(f.due_at)}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
