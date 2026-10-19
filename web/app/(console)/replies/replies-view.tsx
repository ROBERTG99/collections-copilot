"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Confidence, type Source, SourceChip } from "@/components/chips";
import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useHasRole } from "@/components/user-context";
import { type Reclassify, useReclassify, useReplies } from "@/lib/api/hooks";
import type { MessageOut } from "@/lib/api/types";
import { formatDate, formatDateTime, formatMoney, humanize } from "@/lib/format";

const LABELS: Reclassify[] = ["promise_to_pay", "dispute", "wrong_number", "paid", "other"];
const SOURCES: Record<string, Source> = {
  model: "ai",
  rule: "rule",
  fallback: "fallback",
  unknown: "unknown",
};

export function RepliesView() {
  const replies = useReplies();
  return (
    <section aria-labelledby="replies-title">
      <h1 id="replies-title" className="text-xl font-semibold">
        Replies
      </h1>
      <p className="text-sm text-muted">
        Debtor replies classified by the model (STOP is handled by rule before any model sees it).
        An analyst&apos;s correction is shown next to the original label and changes nothing that
        already happened.
      </p>
      <div className="mt-4">
        {replies.isPending ? (
          <Skeleton label="Loading replies" />
        ) : replies.isError ? (
          <ErrorState error={replies.error} onRetry={() => void replies.refetch()} />
        ) : replies.data.length === 0 ? (
          <Empty
            title="No replies yet"
            action={
              <Link href="/queue?status=awaiting_approval" className="underline">
                Approve drafts in the queue
              </Link>
            }
          >
            Replies appear here when debtors answer an approved SMS.
          </Empty>
        ) : (
          <ul className="space-y-2" data-testid="replies">
            {replies.data.map((m) => (
              <ReplyRow key={m.id} message={m} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function ReplyRow({ message: m }: { message: MessageOut }) {
  const canEdit = useHasRole("analyst", "supervisor");
  const [editing, setEditing] = useState(false);
  const promise = (m.extracted?.promise ?? null) as {
    amount_cents?: number;
    promised_date?: string;
  } | null;
  const source = SOURCES[m.classification_source ?? "unknown"] ?? "unknown";
  return (
    <li className="rounded border border-rule bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="rounded border border-rule px-1.5 text-xs font-semibold">
          {humanize(m.classification ?? "unclassified")}
        </span>
        {m.classification ? <SourceChip source={source} /> : null}
        {source === "ai" ? <Confidence value={m.classification_confidence} /> : null}
        {m.corrected_label ? (
          <span className="text-xs">
            corrected to <span className="font-semibold">{humanize(m.corrected_label)}</span> by{" "}
            {m.corrected_by} <SourceChip source="analyst" />
          </span>
        ) : null}
        <span className="text-xs text-muted">
          {m.account_name ? `${m.account_name} · ` : ""}
          {formatDateTime(m.created_at)}
        </span>
        {m.case_id ? (
          <Link href={`/cases/${m.case_id}`} className="ml-auto text-xs underline">
            Case
          </Link>
        ) : null}
      </div>
      <p className="mt-1 whitespace-pre-wrap">{m.body}</p>
      {promise?.amount_cents ? (
        <p className="mt-1 text-sm">
          Extracted promise:{" "}
          <span className="num">{formatMoney(promise.amount_cents, m.currency ?? "USD")}</span> by{" "}
          {formatDate(promise.promised_date)}
        </p>
      ) : null}
      {canEdit && m.status === "handled" && m.classification !== "stop" ? (
        editing ? (
          <ReclassifyForm message={m} onDone={() => setEditing(false)} />
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="mt-2 rounded border border-rule px-2 py-0.5 text-sm"
          >
            Reclassify
          </button>
        )
      ) : null}
    </li>
  );
}

function ReclassifyForm({ message: m, onDone }: { message: MessageOut; onDone: () => void }) {
  const reclassify = useReclassify();
  const select = useRef<HTMLSelectElement>(null);
  const current = (m.corrected_label ?? m.classification ?? "other") as Reclassify;
  const [label, setLabel] = useState<Reclassify>(LABELS.includes(current) ? current : "other");
  useEffect(() => select.current?.focus(), []);
  return (
    <form
      className="mt-2 flex flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        reclassify.mutate({ id: m.id, label }, { onSuccess: onDone });
      }}
    >
      <label className="text-sm">
        Correct label{" "}
        <select
          ref={select}
          value={label}
          onChange={(e) => setLabel(e.target.value as Reclassify)}
          className="rounded border border-rule bg-surface px-1"
        >
          {LABELS.map((l) => (
            <option key={l} value={l}>
              {humanize(l)}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        disabled={reclassify.isPending}
        className="rounded border border-rule px-2 py-0.5 text-sm disabled:opacity-50"
      >
        {reclassify.isPending ? "Saving…" : "Save label"}
      </button>
      <button type="button" onClick={onDone} className="px-2 py-0.5 text-sm underline">
        Cancel
      </button>
      {reclassify.isError ? (
        <div className="basis-full">
          <ErrorState
            error={reclassify.error}
            onRetry={() => reclassify.mutate({ id: m.id, label }, { onSuccess: onDone })}
          />
        </div>
      ) : null}
    </form>
  );
}
