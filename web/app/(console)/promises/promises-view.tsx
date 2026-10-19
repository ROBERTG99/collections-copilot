"use client";

import Link from "next/link";
import { useState } from "react";

import { Modal } from "@/components/modal";
import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useHasRole } from "@/components/user-context";
import { useFollowUps, usePromiseDecision, usePromises } from "@/lib/api/hooks";
import type { PromiseOut } from "@/lib/api/types";
import { formatDate, formatDateTime, formatMoney, humanize } from "@/lib/format";

const GATEWAY: Record<string, string> = {
  approval_required: "Held for a supervisor",
  pending: "Retrying after approval",
  recorded: "Written",
  rejected: "Rejected",
  failed: "Failed",
};

export function PromisesView() {
  const promises = usePromises();
  const followUps = useFollowUps();
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
            <Empty
              title="No promises yet"
              action={
                <Link href="/replies" className="underline">
                  See debtor replies
                </Link>
              }
            >
              They appear when a debtor promises to pay by SMS.
            </Empty>
          ) : (
            <div
              className="overflow-x-auto"
              tabIndex={0}
              role="region"
              aria-label="Promises table (scrollable)"
            >
              <table className="w-full text-sm" data-testid="promises">
                <caption className="sr-only">Promises to pay, newest first</caption>
                <thead>
                  <tr className="border-b border-rule text-left">
                    <th scope="col">Account</th>
                    <th scope="col" className="text-right">
                      Amount
                    </th>
                    <th scope="col">Promised for</th>
                    <th scope="col">Invoices</th>
                    <th scope="col">Gateway</th>
                    <th scope="col">Ledger</th>
                    <th scope="col">Decision</th>
                  </tr>
                </thead>
                <tbody>
                  {promises.data.map((p) => (
                    <PromiseRow key={p.id} promise={p} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
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
          <Empty
            title="Nothing scheduled"
            action={
              <Link href="/queue" className="underline">
                Go to the queue
              </Link>
            }
          >
            Follow-ups are scheduled after each send, deferral, promise and pending approval.
          </Empty>
        ) : (
          <ul className="mt-2 space-y-1 text-sm">
            {followUps.data.map((f) => (
              <li key={f.id}>
                {humanize(f.kind)} · due {formatDateTime(f.due_at)}
                {f.case_id ? (
                  <>
                    {" · "}
                    <Link href={`/cases/${f.case_id}`} className="underline">
                      case {f.case_id.slice(0, 8)}
                    </Link>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function PromiseRow({ promise: p }: { promise: PromiseOut }) {
  const supervisor = useHasRole("supervisor", "admin");
  const [reviewing, setReviewing] = useState(false);
  const held = p.status === "approval_required";
  return (
    <tr className="h-8 border-b border-rule">
      <td>
        <Link href={`/accounts/${p.account_id}`} className="underline">
          {p.account_name ?? "Account"}
        </Link>{" "}
        <span className="num text-xs text-muted">{p.account_ref}</span>
        {p.case_id ? (
          <Link href={`/cases/${p.case_id}`} className="ml-1 text-xs underline">
            case
          </Link>
        ) : null}
      </td>
      <td className="num text-right">{formatMoney(p.amount_cents, p.currency)}</td>
      <td>{formatDate(p.promised_date)}</td>
      <td className="num text-xs">{p.invoice_numbers.join(", ") || "—"}</td>
      <td>
        {GATEWAY[p.status] ?? humanize(p.status)}
        {p.mcp_approval_id ? (
          <span className="num block text-xs text-muted">
            approval {p.mcp_approval_id.slice(0, 8)}
          </span>
        ) : null}
      </td>
      <td className="num text-xs">{p.ledger_ref ?? p.failure_reason ?? "—"}</td>
      <td>
        {held && supervisor ? (
          <button
            type="button"
            onClick={() => setReviewing(true)}
            className="rounded border border-teal px-2 py-0.5 font-semibold text-teal"
          >
            Review
          </button>
        ) : held ? (
          <span className="text-muted">Waiting for a supervisor</span>
        ) : null}
        {reviewing ? <PromiseReview promise={p} onClose={() => setReviewing(false)} /> : null}
      </td>
    </tr>
  );
}

function PromiseReview({ promise: p, onClose }: { promise: PromiseOut; onClose: () => void }) {
  const decide = usePromiseDecision();
  const [reason, setReason] = useState("");
  return (
    <Modal title="Approve a ledger write" onClose={onClose}>
      <p className="text-sm">
        Approving lets the gateway run exactly this call once, with the original idempotency key:
      </p>
      <dl
        className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded border border-rule bg-surface p-3 text-sm"
        data-testid="ledger-preview"
      >
        <dt className="text-muted">Tool</dt>
        <dd className="num">ledger.record_promise_to_pay</dd>
        <dt className="text-muted">Account</dt>
        <dd>
          {p.account_name} <span className="num text-muted">{p.account_ref}</span>
        </dd>
        <dt className="text-muted">Amount</dt>
        <dd className="num">{formatMoney(p.amount_cents, p.currency)}</dd>
        <dt className="text-muted">Promised for</dt>
        <dd>{formatDate(p.promised_date)}</dd>
        <dt className="text-muted">Invoices</dt>
        <dd className="num">{p.invoice_numbers.join(", ") || "—"}</dd>
      </dl>
      {decide.isError ? (
        <ErrorState
          error={decide.error}
          onRetry={decide.variables ? () => decide.mutate(decide.variables) : undefined}
        />
      ) : null}
      {decide.isSuccess ? (
        <p role="status" className="mt-3 text-sm text-teal">
          Decision sent to the gateway.
        </p>
      ) : (
        <div className="mt-4 flex flex-wrap items-end gap-2">
          <button
            type="button"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: p.id, approve: true })}
            className="rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink disabled:opacity-50"
          >
            {decide.isPending ? "Sending…" : "Approve the write"}
          </button>
          <label className="flex flex-col text-sm">
            Reason to reject
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              className="mt-1 w-56 rounded border border-rule bg-surface px-2 py-1"
            />
          </label>
          <button
            type="button"
            disabled={!reason.trim() || decide.isPending}
            onClick={() => decide.mutate({ id: p.id, approve: false, reason: reason.trim() })}
            className="rounded border border-danger px-3 py-1.5 text-danger disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      )}
    </Modal>
  );
}
