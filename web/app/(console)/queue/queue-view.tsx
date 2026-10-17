"use client";

import Link from "next/link";
import { useState } from "react";

import { ComplianceBadge, NbaChip, StatusBadge } from "@/components/chips";
import { Empty, ErrorState, Skeleton } from "@/components/states";
import { type QueueFilters, useQueue, useStartCases } from "@/lib/api/hooks";
import type { QueueItem } from "@/lib/api/types";
import { formatMoney } from "@/lib/format";

const SEGMENTS = [
  { value: "", label: "All segments" },
  { value: "enterprise", label: "Enterprise" },
  { value: "mid_market", label: "Mid-market" },
  { value: "smb", label: "SMB" },
];
const STATUSES = [
  { value: "", label: "Any case status" },
  { value: "no_case", label: "No open case" },
  { value: "awaiting_approval", label: "Awaiting approval" },
  { value: "awaiting_reply", label: "Awaiting reply" },
  { value: "requires_analyst", label: "Needs an analyst" },
];

export function QueueView() {
  const [filters, setFilters] = useState<QueueFilters>({});
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const queue = useQueue(filters);
  const start = useStartCases();
  const items = queue.data?.pages.flatMap((p) => p.items) ?? [];

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <section aria-labelledby="queue-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="queue-title" className="text-xl font-semibold">
            Prioritized queue
          </h1>
          <p className="text-sm text-muted">
            Ordered by the deterministic priority score (balance, days past due, broken promises,
            segment). The model never changes it.
          </p>
        </div>
        <button
          type="button"
          disabled={selected.size === 0 || start.isPending}
          onClick={() => start.mutate([...selected], { onSuccess: () => setSelected(new Set()) })}
          className="rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink disabled:opacity-50"
        >
          {start.isPending ? "Starting…" : `Start cases (${selected.size})`}
        </button>
      </div>

      <form
        role="search"
        aria-label="Filter the queue"
        className="mt-4 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setFilters((f) => ({ ...f, q: search.trim() || undefined }));
        }}
      >
        <label className="flex flex-col text-sm">
          Segment
          <select
            className="mt-1 rounded border border-rule bg-surface px-2 py-1"
            value={filters.segment ?? ""}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                segment: (e.target.value || undefined) as QueueFilters["segment"],
              }))
            }
          >
            {SEGMENTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          Case status
          <select
            className="mt-1 rounded border border-rule bg-surface px-2 py-1"
            value={filters.status ?? ""}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                status: (e.target.value || undefined) as QueueFilters["status"],
              }))
            }
          >
            {STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          Minimum score
          <input
            type="number"
            min={0}
            max={100}
            inputMode="numeric"
            className="num mt-1 w-24 rounded border border-rule bg-surface px-2 py-1"
            value={filters.min_score ?? ""}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                min_score: e.target.value === "" ? undefined : Number(e.target.value),
              }))
            }
          />
        </label>
        <label className="flex flex-col text-sm">
          Account or reference
          <input
            type="search"
            className="mt-1 rounded border border-rule bg-surface px-2 py-1"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <button type="submit" className="rounded border border-rule px-3 py-1">
          Search
        </button>
      </form>

      {start.isError ? <ErrorState error={start.error} /> : null}
      {start.isSuccess ? (
        <p role="status" className="mt-3 text-sm">
          {start.data.cases.length} case(s) queued. Drafts appear on each account when ready.
        </p>
      ) : null}

      <div className="mt-4">
        {queue.isPending ? (
          <Skeleton label="Loading the queue" />
        ) : queue.isError ? (
          <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />
        ) : items.length === 0 ? (
          <Empty title="No accounts match these filters">
            Clear the filters, or ingest an aging file to fill the queue.
          </Empty>
        ) : (
          <>
            <QueueTable items={items} selected={selected} onToggle={toggle} />
            <QueueCards items={items} />
            {queue.hasNextPage ? (
              <button
                type="button"
                onClick={() => void queue.fetchNextPage()}
                disabled={queue.isFetchingNextPage}
                className="mt-3 rounded border border-rule px-3 py-1"
              >
                {queue.isFetchingNextPage ? "Loading…" : "Load more"}
              </button>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}

function QueueTable({
  items,
  selected,
  onToggle,
}: {
  items: QueueItem[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <table className="hidden w-full border-collapse text-sm md:table">
      <caption className="sr-only">Accounts by priority, highest first</caption>
      <thead>
        <tr className="border-b border-rule text-left">
          <th scope="col" className="w-8 py-1">
            <span className="sr-only">Select</span>
          </th>
          <th scope="col">Account</th>
          <th scope="col">Segment</th>
          <th scope="col" className="text-right">
            Open balance
          </th>
          <th scope="col" className="text-right">
            Days past due
          </th>
          <th scope="col" className="text-right">
            Score
          </th>
          <th scope="col">Next best action</th>
          <th scope="col">Compliance</th>
          <th scope="col">Case</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.account_id} className="h-8 border-b border-rule hover:bg-surface-2">
            <td>
              <input
                type="checkbox"
                aria-label={`Select ${item.name}`}
                disabled={!!item.case_id || item.opted_out}
                checked={selected.has(item.account_id)}
                onChange={() => onToggle(item.account_id)}
              />
            </td>
            <td>
              <Link href={`/accounts/${item.account_id}`} className="font-semibold underline">
                {item.name}
              </Link>{" "}
              <span className="num text-xs text-muted">{item.external_ref}</span>
              {item.opted_out ? <span className="ml-1 text-xs text-danger">opted out</span> : null}
            </td>
            <td>{item.segment.replace("_", "-")}</td>
            <td className="num text-right">
              {formatMoney(item.open_balance.amount_cents, item.open_balance.currency)}
            </td>
            <td className="num text-right">{item.max_days_past_due}</td>
            <td className="num text-right" title={scoreTitle(item)}>
              {item.priority_score}
            </td>
            <td>
              <NbaChip
                action={item.nba_action}
                confidence={item.nba_confidence}
                fallback={item.fallback}
              />
            </td>
            <td>
              <ComplianceBadge ok={item.compliance_ok} codes={item.compliance_codes} />
            </td>
            <td>
              <StatusBadge status={item.case_status} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function QueueCards({ items }: { items: QueueItem[] }) {
  return (
    <ul className="space-y-2 md:hidden">
      {items.map((item) => (
        <li key={item.account_id} className="rounded border border-rule bg-surface p-3">
          <div className="flex items-baseline justify-between gap-2">
            <Link href={`/accounts/${item.account_id}`} className="font-semibold underline">
              {item.name}
            </Link>
            <span className="num" aria-label={`score ${item.priority_score}`}>
              {item.priority_score}
            </span>
          </div>
          <p className="num text-sm">
            {formatMoney(item.open_balance.amount_cents, item.open_balance.currency)} ·{" "}
            {item.max_days_past_due} days
          </p>
          <div className="mt-1 flex flex-wrap gap-2">
            <NbaChip
              action={item.nba_action}
              confidence={item.nba_confidence}
              fallback={item.fallback}
            />
            <StatusBadge status={item.case_status} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function scoreTitle(item: QueueItem): string {
  const c = item.score_components ?? {};
  return Object.entries(c)
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`)
    .join(" · ");
}
