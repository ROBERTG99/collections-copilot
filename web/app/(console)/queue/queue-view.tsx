"use client";

import Link from "next/link";
import { useState } from "react";

import { ComplianceBadge, NbaChip, StatusBadge } from "@/components/chips";
import { Modal } from "@/components/modal";
import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useHasRole } from "@/components/user-context";
import {
  MAX_BATCH,
  type QueueFilters,
  useBatchApprove,
  useQueue,
  useStartCases,
} from "@/lib/api/hooks";
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

/** A row can be selected to start a case, or to approve its draft in a batch. */
function startable(item: QueueItem): boolean {
  return !item.case_id && !item.opted_out;
}
function approvable(item: QueueItem): boolean {
  return item.case_status === "awaiting_approval" && item.draft_version != null;
}

export function QueueView() {
  const [filters, setFilters] = useState<QueueFilters>({});
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reviewing, setReviewing] = useState(false);
  const queue = useQueue(filters);
  const start = useStartCases();
  const canAct = useHasRole("analyst", "supervisor");
  const items = queue.data?.pages.flatMap((p) => p.items) ?? [];
  const picked = items.filter((i) => selected.has(i.account_id));
  const toStart = picked.filter(startable);
  const toApprove = picked.filter(approvable);
  const filtered = Object.values(filters).some((v) => v !== undefined && v !== "");

  function clearFilters() {
    setFilters({});
    setSearch("");
  }

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
        {canAct ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={toStart.length === 0 || start.isPending}
              onClick={() =>
                start.mutate(
                  toStart.map((i) => i.account_id),
                  { onSuccess: () => setSelected(new Set()) },
                )
              }
              className="rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink disabled:opacity-50"
            >
              {start.isPending ? "Starting…" : `Start cases (${toStart.length})`}
            </button>
            <button
              type="button"
              disabled={toApprove.length === 0 || toApprove.length > MAX_BATCH}
              onClick={() => setReviewing(true)}
              className="rounded border border-teal px-3 py-1.5 font-semibold text-teal disabled:opacity-50"
            >
              Review and approve ({toApprove.length})
            </button>
          </div>
        ) : null}
      </div>
      {toApprove.length > MAX_BATCH ? (
        <p className="mt-2 text-sm text-danger">
          Select at most {MAX_BATCH} drafts to approve at once ({toApprove.length} selected).
        </p>
      ) : null}

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

      {start.isError ? (
        <ErrorState
          error={start.error}
          onRetry={start.variables ? () => start.mutate(start.variables) : undefined}
        />
      ) : null}
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
          <Empty
            title={filtered ? "No accounts match these filters" : "The queue is empty"}
            action={
              filtered ? (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="rounded border border-rule px-3 py-1"
                >
                  Clear filters
                </button>
              ) : null
            }
          >
            {filtered
              ? "Widen or clear the filters."
              : "Ingest an aging file (POST /api/v1/ingest/aging or make seed) to fill the queue."}
          </Empty>
        ) : (
          <>
            <QueueTable items={items} selected={selected} onToggle={toggle} canAct={canAct} />
            <QueueCards items={items} selected={selected} onToggle={toggle} canAct={canAct} />
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
      {reviewing ? (
        <BatchReview
          items={toApprove}
          onClose={() => setReviewing(false)}
          onDone={() => setSelected(new Set())}
        />
      ) : null}
    </section>
  );
}

function BatchReview({
  items: selectedItems,
  onClose,
  onDone,
}: {
  items: QueueItem[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [items] = useState(selectedItems); // what was reviewed, even after the queue refreshes
  const batch = useBatchApprove();
  const results = new Map((batch.data?.results ?? []).map((r) => [r.case_id, r]));
  return (
    <Modal title={`Approve ${items.length} SMS drafts`} onClose={onClose}>
      <p className="text-sm text-muted">
        Each message below is sent exactly as shown, to the account&apos;s number, after the
        compliance post-check passes again at approval and at send time. Anything that fails is
        reported per message and left for review.
      </p>
      <ol className="mt-3 space-y-2" data-testid="batch-items">
        {items.map((item) => {
          const r = item.case_id ? results.get(item.case_id) : undefined;
          return (
            <li key={item.account_id} className="rounded border border-rule bg-surface p-2 text-sm">
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{item.name}</span>
                <span className="num text-xs text-muted">
                  {item.external_ref} · draft v{item.draft_version}
                </span>
                {r ? (
                  <span
                    className={`ml-auto text-xs font-semibold ${r.status < 300 ? "text-teal" : "text-danger"}`}
                  >
                    {r.status < 300
                      ? "Approved"
                      : `Not approved: ${String(r.error?.detail ?? r.error?.title ?? r.status)}`}
                  </span>
                ) : null}
              </p>
              <p className="mt-1 whitespace-pre-wrap">{item.draft_body}</p>
            </li>
          );
        })}
      </ol>
      {batch.isError ? (
        <ErrorState
          error={batch.error}
          onRetry={batch.variables ? () => batch.mutate(batch.variables) : undefined}
        />
      ) : null}
      <div className="mt-4 flex gap-2">
        {batch.isSuccess ? (
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-rule px-3 py-1.5"
          >
            Done
          </button>
        ) : (
          <button
            type="button"
            disabled={batch.isPending}
            onClick={() =>
              batch.mutate(
                items.map((i) => ({
                  case_id: i.case_id ?? "",
                  draft_version: i.draft_version ?? 0,
                })),
                { onSuccess: onDone },
              )
            }
            className="rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink disabled:opacity-50"
          >
            {batch.isPending ? "Approving…" : `Approve ${items.length} messages`}
          </button>
        )}
      </div>
    </Modal>
  );
}

type RowsProps = {
  items: QueueItem[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  canAct: boolean;
};

function SelectBox({
  item,
  selected,
  onToggle,
  canAct,
}: Omit<RowsProps, "items"> & { item: QueueItem }) {
  if (!canAct) return null;
  const label = approvable(item)
    ? `Select the draft for ${item.name}`
    : `Select ${item.name} to start a case`;
  return (
    <input
      type="checkbox"
      aria-label={label}
      disabled={!startable(item) && !approvable(item)}
      checked={selected.has(item.account_id)}
      onChange={() => onToggle(item.account_id)}
      className="size-5"
    />
  );
}

function QueueTable({ items, selected, onToggle, canAct }: RowsProps) {
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
              <SelectBox item={item} selected={selected} onToggle={onToggle} canAct={canAct} />
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
              <span className="sr-only"> ({scoreTitle(item)})</span>
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

function QueueCards({ items, selected, onToggle, canAct }: RowsProps) {
  return (
    <ul className="space-y-2 md:hidden">
      {items.map((item) => (
        <li key={item.account_id} className="rounded border border-rule bg-surface p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2">
              <SelectBox item={item} selected={selected} onToggle={onToggle} canAct={canAct} />
              <Link href={`/accounts/${item.account_id}`} className="font-semibold underline">
                {item.name}
              </Link>
            </span>
            <span className="num">
              <span className="sr-only">score </span>
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
            <ComplianceBadge ok={item.compliance_ok} codes={item.compliance_codes} />
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
