import { humanize, percent } from "@/lib/format";

const base = "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold";

export type Source = "ai" | "model" | "fallback" | "analyst" | "rule" | "unknown";

/**
 * Who produced a value. Model output is always labelled "AI draft"; deterministic fallbacks say
 * "Template"; a person's edit or correction says so (BUILD.md §13 AI UX).
 */
export function SourceChip({ source, fallback }: { source?: Source | null; fallback?: boolean }) {
  const kind: Source = source ?? (fallback ? "fallback" : "ai");
  if (kind === "fallback") {
    return (
      <span
        className={`${base} bg-chip-template text-chip-template-ink`}
        title="Rules or template, no model"
      >
        Template
      </span>
    );
  }
  if (kind === "analyst") {
    return (
      <span className={`${base} border border-rule`} title="Written or corrected by a person">
        Edited by analyst
      </span>
    );
  }
  if (kind === "rule") {
    return (
      <span className={`${base} border border-rule`} title="Decided by a deterministic rule">
        Rule
      </span>
    );
  }
  if (kind === "unknown") {
    return <span className={`${base} border border-rule`}>Source not recorded</span>;
  }
  return (
    <span
      className={`${base} bg-chip-ai text-teal`}
      title="Suggested by a model; a person approves"
    >
      AI draft
    </span>
  );
}

/** A model's confidence as a number, read out with its meaning. */
export function Confidence({ value }: { value: number | null | undefined }) {
  return (
    <span className="num text-xs">
      <span className="sr-only">confidence </span>
      {percent(value)}
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
      <Confidence value={confidence} />
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
