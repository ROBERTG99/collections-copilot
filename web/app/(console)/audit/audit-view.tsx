"use client";

import { useState } from "react";

import { Empty, ErrorState, Skeleton } from "@/components/states";
import { api, unwrap } from "@/lib/api/client";
import { useAudit } from "@/lib/api/hooks";
import type { AuditEvent } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

const EXPORT_MAX = 20_000;

/** CSV cell; text a spreadsheet would run as a formula (= + - @, tab, CR) is prefixed with '. */
export function csvCell(value: unknown): string {
  let text = String(value ?? "");
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

/** Every matching event, newest first, page by page (capped at EXPORT_MAX). */
async function fetchAll(action: string | undefined): Promise<AuditEvent[]> {
  const out: AuditEvent[] = [];
  let before: number | null | undefined = undefined;
  while (out.length < EXPORT_MAX) {
    const page: { items: AuditEvent[]; next_before_seq?: number | null } = unwrap(
      await api.GET("/api/v1/audit", {
        params: { query: { limit: 500, action: action || undefined, before_seq: before } },
      }),
    );
    out.push(...page.items);
    before = page.next_before_seq;
    if (before == null || page.items.length === 0) break;
  }
  return out;
}

function toCsv(rows: AuditEvent[]): string {
  const cell = csvCell;
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

export function AuditView() {
  const [action, setAction] = useState("");
  const [applied, setApplied] = useState<string | undefined>(undefined);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);
  const audit = useAudit(applied);
  const items = audit.data?.items ?? [];

  async function exportCsv() {
    setExporting(true);
    setExportError(null);
    try {
      const rows = await fetchAll(applied);
      const url = URL.createObjectURL(new Blob([toCsv(rows)], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "audit.csv";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000); // after the download has started
    } catch (error) {
      setExportError(error);
    } finally {
      setExporting(false);
    }
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
          onClick={() => void exportCsv()}
          disabled={items.length === 0 || exporting}
          className="rounded border border-rule px-3 py-1 disabled:opacity-50"
        >
          {exporting ? "Exporting…" : "Export CSV (all matching events)"}
        </button>
      </form>
      {exportError ? <ErrorState error={exportError} onRetry={() => void exportCsv()} /> : null}
      <div className="mt-4">
        {audit.isPending ? (
          <Skeleton label="Loading the audit log" />
        ) : audit.isError ? (
          <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
        ) : items.length === 0 ? (
          <Empty
            title="No events match"
            action={
              applied ? (
                <button
                  type="button"
                  onClick={() => {
                    setAction("");
                    setApplied(undefined);
                  }}
                  className="rounded border border-rule px-3 py-1"
                >
                  Clear the filter
                </button>
              ) : null
            }
          >
            {applied ? "No event has this action." : "Nothing has been audited yet."}
          </Empty>
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
                    <td className="max-w-md text-xs">
                      <details>
                        <summary className="cursor-pointer truncate">
                          {JSON.stringify(e.payload)}
                        </summary>
                        <pre className="mt-1 whitespace-pre-wrap break-all">
                          {JSON.stringify(e.payload, null, 2)}
                        </pre>
                      </details>
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
