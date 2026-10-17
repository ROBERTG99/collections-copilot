"use client";

import Link from "next/link";
import { useState } from "react";

import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useHasRole } from "@/components/user-context";
import { type Reclassify, useReclassify, useReplies } from "@/lib/api/hooks";
import { formatDateTime, formatMoney, humanize, percent } from "@/lib/format";

const LABELS: Reclassify[] = ["promise_to_pay", "dispute", "wrong_number", "paid", "other"];

export default function RepliesPage() {
  const replies = useReplies();
  const reclassify = useReclassify();
  const canEdit = useHasRole("analyst", "supervisor");
  const [editing, setEditing] = useState<string | null>(null);
  return (
    <section aria-labelledby="replies-title">
      <h1 id="replies-title" className="text-xl font-semibold">
        Replies
      </h1>
      <p className="text-sm text-muted">
        Debtor replies classified by the model (STOP is handled by rule before any model sees it).
      </p>
      <div className="mt-4">
        {replies.isPending ? (
          <Skeleton label="Loading replies" />
        ) : replies.isError ? (
          <ErrorState error={replies.error} onRetry={() => void replies.refetch()} />
        ) : replies.data.length === 0 ? (
          <Empty title="No replies yet">
            Replies appear here when debtors answer an approved SMS.
          </Empty>
        ) : (
          <ul className="space-y-2" data-testid="replies">
            {replies.data.map((m) => {
              const promise = (m.extracted?.promise ?? null) as {
                amount_cents?: number;
                promised_date?: string;
              } | null;
              return (
                <li key={m.id} className="rounded border border-rule bg-surface p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="rounded border border-rule px-1.5 text-xs font-semibold">
                      {humanize(m.classification ?? "unclassified")}
                    </span>
                    <span className="num text-xs">{percent(m.classification_confidence)}</span>
                    <span className="text-xs text-muted">{formatDateTime(m.created_at)}</span>
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
                      <span className="num">{formatMoney(promise.amount_cents, "USD")}</span> by{" "}
                      {promise.promised_date}
                    </p>
                  ) : null}
                  {canEdit ? (
                    editing === m.id ? (
                      <form
                        className="mt-2 flex flex-wrap items-center gap-2"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const label = new FormData(e.currentTarget).get("label") as Reclassify;
                          reclassify.mutate(
                            { id: m.id, label },
                            { onSuccess: () => setEditing(null) },
                          );
                        }}
                      >
                        <label className="text-sm">
                          New label{" "}
                          <select
                            name="label"
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
                          className="rounded border border-rule px-2 py-0.5 text-sm"
                        >
                          Save label
                        </button>
                      </form>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setEditing(m.id)}
                        className="mt-2 rounded border border-rule px-2 py-0.5 text-sm"
                      >
                        Reclassify
                      </button>
                    )
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        {reclassify.isError ? <ErrorState error={reclassify.error} /> : null}
      </div>
    </section>
  );
}
