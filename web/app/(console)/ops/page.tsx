"use client";

import Link from "next/link";
import { useState } from "react";

import { ErrorState, Skeleton } from "@/components/states";
import { useOps } from "@/lib/api/hooks";
import { humanize, percent } from "@/lib/format";

function Card({
  title,
  value,
  href,
  testid,
}: {
  title: string;
  value: string;
  href?: string;
  testid: string;
}) {
  return (
    <div className="rounded border border-rule bg-surface p-3">
      <p className="text-sm text-muted">{title}</p>
      <p className="num text-2xl" data-testid={testid}>
        {value}
      </p>
      {href ? (
        <Link href={href} className="text-xs underline">
          View source
        </Link>
      ) : null}
    </div>
  );
}

export default function OpsPage() {
  const [windowDays, setWindowDays] = useState(30);
  const ops = useOps(windowDays);
  return (
    <section aria-labelledby="ops-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 id="ops-title" className="text-xl font-semibold">
          Operations
        </h1>
        <label className="text-sm">
          Window{" "}
          <select
            value={windowDays}
            onChange={(e) => setWindowDays(Number(e.target.value))}
            className="rounded border border-rule bg-surface px-1"
          >
            {[7, 30, 90].map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="mt-4">
        {ops.isPending ? (
          <Skeleton rows={6} label="Loading metrics" />
        ) : ops.isError ? (
          <ErrorState error={ops.error} onRetry={() => void ops.refetch()} />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Card
                testid="pending-approvals"
                title="Pending approvals"
                value={String(ops.data.pending_approvals)}
                href="/queue"
              />
              <Card
                testid="window-rate"
                title="Sends inside the contact window"
                value={
                  ops.data.contact_window_rate == null ? "—" : percent(ops.data.contact_window_rate)
                }
              />
              <Card
                testid="cost-per-account"
                title="Model cost per account"
                value={
                  ops.data.cost_per_account_usd == null
                    ? "—"
                    : `$${ops.data.cost_per_account_usd.toFixed(4)}` // sub-cent model costs
                }
              />
              <Card
                testid="sends"
                title={`SMS sent (${windowDays} d)`}
                value={String(ops.data.sends)}
              />
              <Card
                testid="replies"
                title="Replies"
                value={String(ops.data.replies)}
                href="/replies"
              />
              <Card
                testid="opt-outs"
                title="Opt-outs"
                value={String(ops.data.opt_outs)}
                href="/settings"
              />
              <Card
                testid="follow-ups"
                title="Follow-ups pending"
                value={String(ops.data.follow_ups_pending)}
                href="/promises"
              />
              <Card
                testid="dlq"
                title="Dead-letter messages (platform)"
                value={Object.values(ops.data.dlq_depth)
                  .map((v) => (v == null ? "?" : String(v)))
                  .join(" / ")}
              />
            </div>
            <div className="mt-6 grid gap-6 md:grid-cols-2">
              <section aria-labelledby="by-status">
                <h2 id="by-status" className="font-semibold">
                  Cases by status
                </h2>
                <table className="mt-2 w-full text-sm" data-testid="cases-by-status">
                  <tbody>
                    {Object.entries(ops.data.cases_by_status).map(([status, n]) => (
                      <tr key={status} className="h-8 border-b border-rule">
                        <th scope="row" className="text-left font-normal">
                          {humanize(status)}
                        </th>
                        <td className="num text-right">{n}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
              <section aria-labelledby="p95">
                <h2 id="p95" className="font-semibold">
                  Node latency p95
                </h2>
                <table className="mt-2 w-full text-sm">
                  <tbody>
                    {Object.entries(ops.data.node_p95_ms).map(([node, ms]) => (
                      <tr key={node} className="h-8 border-b border-rule">
                        <th scope="row" className="text-left font-normal">
                          {humanize(node)}
                        </th>
                        <td className="num text-right">{ms} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
