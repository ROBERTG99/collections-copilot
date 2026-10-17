"use client";

import Link from "next/link";

import { NbaChip, StatusBadge } from "@/components/chips";
import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useAccount, useStartCases } from "@/lib/api/hooks";
import type { AccountDetail } from "@/lib/api/types";
import { formatDate, formatDateTime, formatMoney, humanize } from "@/lib/format";

const OPEN = new Set([
  "queued",
  "processing",
  "awaiting_approval",
  "scheduled",
  "sent",
  "awaiting_reply",
  "requires_analyst",
  "escalated",
]);

export function AccountView({ accountId }: { accountId: string }) {
  const account = useAccount(accountId);
  if (account.isPending) return <Skeleton rows={12} label="Loading the account" />;
  if (account.isError)
    return <ErrorState error={account.error} onRetry={() => void account.refetch()} />;
  return <AccountBody account={account.data} />;
}

function AccountBody({ account }: { account: AccountDetail }) {
  const start = useStartCases();
  const openCase = account.cases.find((c) => OPEN.has(c.status));
  const money = (cents: number) => formatMoney(cents, account.currency);
  return (
    <article aria-labelledby="account-title" className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 id="account-title" className="text-xl font-semibold">
            {account.name}
          </h1>
          <p className="num text-sm text-muted">
            {account.external_ref} · {humanize(account.segment)} · {account.language.toUpperCase()}{" "}
            · {account.timezone}
          </p>
          {account.opted_out ? (
            <p className="mt-1 font-semibold text-danger">Opted out of SMS</p>
          ) : null}
          {account.contact_flag ? (
            <p className="mt-1 font-semibold text-danger">
              Contact flagged: {humanize(account.contact_flag)}
            </p>
          ) : null}
        </div>
        <div className="text-right">
          <p className="text-sm text-muted">Priority score</p>
          <p className="num text-3xl" data-testid="priority-score">
            {account.priority_score}
          </p>
          {openCase ? (
            <Link
              href={`/cases/${openCase.id}`}
              className="mt-2 inline-block rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink"
            >
              Open case ({humanize(openCase.status)})
            </Link>
          ) : (
            <button
              type="button"
              disabled={start.isPending || account.opted_out}
              onClick={() => start.mutate([account.id])}
              className="mt-2 rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink disabled:opacity-50"
            >
              {start.isPending ? "Starting…" : "Start case"}
            </button>
          )}
          {start.isError ? <ErrorState error={start.error} /> : null}
        </div>
      </header>

      <section
        aria-labelledby="precheck-title"
        className="rounded border border-rule bg-surface p-3"
      >
        <h2 id="precheck-title" className="font-semibold">
          Compliance pre-check (now)
        </h2>
        {account.precheck.ok ? (
          <p className="text-teal">Clear to contact.</p>
        ) : (
          <p className="text-danger">Contact is blocked.</p>
        )}
        <ul className="mt-1 list-disc pl-5 text-sm">
          {account.precheck.violations.map((v, i) => (
            <li key={i}>
              <span className="font-semibold">{humanize(String(v.code))}</span>: {String(v.message)}
              {v.blocking ? "" : " (not blocking)"}
            </li>
          ))}
        </ul>
        <p className="mt-1 text-sm text-muted">
          Earliest send: {formatDateTime(account.precheck.send_at)}
          {account.precheck.deferred ? " (outside the 08:00–21:00 window now)" : ""}
        </p>
      </section>

      <section aria-labelledby="aging-title">
        <h2 id="aging-title" className="font-semibold">
          Aging · open balance{" "}
          <span className="num">{money(account.open_balance.amount_cents)}</span>
        </h2>
        <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">
          {(
            [
              ["Current", account.aging.current],
              ["1–30", account.aging.d1_30],
              ["31–60", account.aging.d31_60],
              ["61–90", account.aging.d61_90],
              ["90+", account.aging.d90_plus],
            ] as const
          ).map(([label, cents]) => (
            <div key={label} className="rounded border border-rule bg-surface p-2">
              <dt className="text-xs text-muted">{label} days</dt>
              <dd className="num">{money(cents ?? 0)}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-labelledby="invoices-title">
        <h2 id="invoices-title" className="font-semibold">
          Invoices
        </h2>
        {account.invoices.length === 0 ? (
          <Empty title="No invoices on file" />
        ) : (
          <div className="overflow-x-auto">
            <table className="mt-2 w-full text-sm">
              <thead>
                <tr className="border-b border-rule text-left">
                  <th scope="col">Number</th>
                  <th scope="col" className="text-right">
                    Amount
                  </th>
                  <th scope="col">Due</th>
                  <th scope="col" className="text-right">
                    Days past due
                  </th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {account.invoices.map((inv) => (
                  <tr key={inv.number} className="h-8 border-b border-rule">
                    <td className="num">{inv.number}</td>
                    <td className="num text-right">
                      {formatMoney(inv.amount_cents, inv.currency)}
                    </td>
                    <td>{formatDate(inv.due_on)}</td>
                    <td className="num text-right">{inv.days_past_due}</td>
                    <td>
                      {humanize(inv.status)}
                      {inv.dispute_reason ? (
                        <span className="text-muted"> — {inv.dispute_reason}</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="cases-title">
        <h2 id="cases-title" className="font-semibold">
          Next best action and history
        </h2>
        {account.cases.length === 0 ? (
          <Empty title="No cases yet">Start a case to get a next best action and a draft.</Empty>
        ) : (
          <ul className="mt-2 space-y-2">
            {account.cases.map((c) => (
              <li key={c.id} className="rounded border border-rule bg-surface p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={c.status} />
                  <NbaChip
                    action={c.nba_action}
                    confidence={c.nba_confidence}
                    fallback={c.fallback}
                  />
                  <span className="text-xs text-muted">{formatDateTime(c.created_at)}</span>
                  <Link href={`/cases/${c.id}`} className="ml-auto text-sm underline">
                    View case
                  </Link>
                </div>
                {c.nba_rationale ? <p className="mt-1 text-sm">{c.nba_rationale}</p> : null}
                {c.cited_note_ids.length ? (
                  <p className="mt-1 text-sm">
                    Cites:{" "}
                    {c.cited_note_ids.map((id, i) => (
                      <a key={id} href={`#note-${id}`} className="underline">
                        note {i + 1}
                      </a>
                    ))}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="notes-title">
        <h2 id="notes-title" className="font-semibold">
          Notes{" "}
          <span className="text-xs font-normal text-muted">
            (untrusted text from CRM and replies)
          </span>
        </h2>
        {account.notes.length === 0 ? (
          <Empty title="No notes" />
        ) : (
          <ul className="mt-2 space-y-2">
            {account.notes.map((n) => (
              <li
                key={n.id}
                id={`note-${n.id}`}
                tabIndex={-1}
                className="rounded border border-rule bg-surface p-2 text-sm target:outline target:outline-2 target:outline-teal"
              >
                <p className="text-xs text-muted">
                  {humanize(n.source)} · {formatDateTime(n.created_at)}
                </p>
                <p className="whitespace-pre-wrap">{n.body}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        <section aria-labelledby="messages-title">
          <h2 id="messages-title" className="font-semibold">
            Messages
          </h2>
          {account.messages.length === 0 ? (
            <Empty title="No messages yet" />
          ) : (
            <ul className="mt-2 space-y-2">
              {account.messages.map((m) => (
                <li key={m.id} className="rounded border border-rule bg-surface p-2 text-sm">
                  <p className="text-xs text-muted">
                    {m.direction === "outbound" ? "Sent" : "Received"} · {humanize(m.status)} ·{" "}
                    {formatDateTime(m.sent_at ?? m.created_at)}
                    {m.classification ? ` · ${humanize(m.classification)}` : ""}
                  </p>
                  <p className="whitespace-pre-wrap">{m.body}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section aria-labelledby="promises-title">
          <h2 id="promises-title" className="font-semibold">
            Promises to pay
          </h2>
          {account.promises.length === 0 ? (
            <Empty title="No promises" />
          ) : (
            <ul className="mt-2 space-y-2">
              {account.promises.map((p) => (
                <li key={p.id} className="rounded border border-rule bg-surface p-2 text-sm">
                  <span className="num">{formatMoney(p.amount_cents, p.currency)}</span> by{" "}
                  {formatDate(p.promised_date)} · {humanize(p.status)}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </article>
  );
}
