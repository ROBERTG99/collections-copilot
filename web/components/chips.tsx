import { humanize, percent } from "@/lib/format";

const base = "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold";

/** Model output is always labelled; deterministic fallbacks say so (BUILD.md §13 AI UX). */
export function SourceChip({ fallback }: { fallback: boolean }) {
  return fallback ? (
    <span className={`${base} bg-chip-template text-amber`} title="Rules or template, no model">
      Template
    </span>
  ) : (
    <span
      className={`${base} bg-chip-ai text-teal`}
      title="Suggested by a model; a person approves"
    >
      AI draft
    </span>
  );
}

export function NbaChip({
  action,
  confidence,
  fallback,
}: {
  action: string | null | undefined;
  confidence: number | null | undefined;
  fallback: boolean;
}) {
  if (!action) return <span className="text-muted">—</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className={`${base} border border-rule`}>{humanize(action)}</span>
      <span className="num text-xs" aria-label={`confidence ${percent(confidence)}`}>
        {percent(confidence)}
      </span>
      <SourceChip fallback={fallback} />
    </span>
  );
}

export function ComplianceBadge({
  ok,
  codes,
}: {
  ok: boolean | null | undefined;
  codes: string[];
}) {
  if (ok == null) return <span className="text-muted">not checked</span>;
  return ok ? (
    <span className={`${base} border border-teal text-teal`}>Clear</span>
  ) : (
    <span className={`${base} border border-danger text-danger`} title={codes.join(", ")}>
      Blocked: {codes.map(humanize).join(", ")}
    </span>
  );
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  return <span className={`${base} bg-surface-2`}>{status ? humanize(status) : "no case"}</span>;
}
