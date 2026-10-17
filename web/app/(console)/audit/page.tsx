"use client";

import { useState } from "react";

import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useAudit } from "@/lib/api/hooks";
import { formatDateTime } from "@/lib/format";

type AuditEvent = {
  seq: number;
  ts: string;
  actor_type: string;
  actor_id: string;
  action: string;
  entity: string;
  entity_id: string | null;
  payload: Record<string, unknown>;
  hash: string;
};

function toCsv(rows: AuditEvent[]): string {
  const cell = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  const header = [
    "seq",
    "ts",
    "actor_type",
    "actor_id",
    "action",
    "entity",
    "entity_id",
    "payload",
    "hash",
  ];
  const lines = rows.map((r) =>
    [
      r.seq,
      r.ts,
      r.actor_type,
      r.actor_id,
      r.action,
      r.entity,
      r.entity_id,
      JSON.stringify(r.payload),
      r.hash,
    ]
      .map(cell)
      .join(","),
  );
  return [header.join(","), ...lines].join("\n");
}

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [applied, setApplied] = useState<string | undefined>(undefined);
  const audit = useAudit(applied);
  const items = (audit.data?.items ?? []) as AuditEvent[];

  function exportCsv() {
    const url = URL.createObjectURL(new Blob([toCsv(items)], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "audit.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section aria-labelledby="audit-title">
      <h1 id="audit-title" className="text-xl font-semibold">
        Audit log
      </h1>
      {audit.data ? (
        audit.data.chain_ok ? (
          <p
            role="status"
            className="mt-2 rounded border border-teal p-2 text-teal"
            data-testid="chain"
          >
            Hash chain verified: no event was changed, removed or reordered.
          </p>
        ) : (
          <p
            role="alert"
            className="mt-2 rounded border border-danger p-2 text-danger"
            data-testid="chain"
          >
            Hash chain broken at event {audit.data.first_bad_seq}.
          </p>
        )
      ) : null}
      <form
        className="mt-3 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(action.trim() || undefined);
        }}
      >
        <label className="flex flex-col text-sm">
          Action (for example approval.edit)
          <input
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="mt-1 rounded border border-rule bg-surface px-2 py-1"
          />
        </label>
        <button type="submit" className="rounded border border-rule px-3 py-1">
          Filter
        </button>
        <button
          type="button"
          onClick={exportCsv}
          disabled={items.length === 0}
          className="rounded border border-rule px-3 py-1 disabled:opacity-50"
        >
          Export CSV
        </button>
      </form>
      <div className="mt-4">
        {audit.isPending ? (
          <Skeleton label="Loading the audit log" />
        ) : audit.isError ? (
          <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
        ) : items.length === 0 ? (
          <Empty title="No events match">Clear the filter to see every event.</Empty>
        ) : (
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
            <table className="w-full text-sm">
              <caption className="sr-only">Audit events, newest first</caption>
              <thead>
                <tr className="border-b border-rule text-left">
                  <th scope="col">#</th>
                  <th scope="col">When</th>
                  <th scope="col">Actor</th>
                  <th scope="col">Action</th>
                  <th scope="col">Entity</th>
                  <th scope="col">Details</th>
                </tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.seq} className="h-8 border-b border-rule align-top">
                    <td className="num">{e.seq}</td>
                    <td className="whitespace-nowrap">{formatDateTime(e.ts)}</td>
                    <td>{e.actor_id}</td>
                    <td className="num">{e.action}</td>
                    <td className="num text-xs">
                      {e.entity} {e.entity_id?.slice(0, 8)}
                    </td>
                    <td className="max-w-md truncate text-xs" title={JSON.stringify(e.payload)}>
                      {JSON.stringify(e.payload)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
