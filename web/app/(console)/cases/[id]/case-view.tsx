"use client";

import Link from "next/link";
import { useState } from "react";

import { ApprovalDrawer } from "@/components/approval-drawer/approval-drawer";
import { NbaChip, SourceChip, StatusBadge } from "@/components/chips";
import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useCaseEvents } from "@/lib/api/events";
import { useCase } from "@/lib/api/hooks";
import { formatDateTime, humanize } from "@/lib/format";

export function CaseView({ caseId }: { caseId: string }) {
  const detail = useCase(caseId);
  const { events, connected } = useCaseEvents(caseId);
  const [open, setOpen] = useState(false);

  if (detail.isPending) return <Skeleton rows={10} label="Loading the case" />;
  if (detail.isError)
    return <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />;
  const d = detail.data;
  const latest = d.drafts[d.drafts.length - 1];
  const awaiting = d.case.status === "awaiting_approval";

  return (
    <article aria-labelledby="case-title" className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm">
            <Link href={`/accounts/${d.account.account_id}`} className="underline">
              {d.account.name}
            </Link>{" "}
            <span className="num text-muted">{d.account.external_ref}</span>
          </p>
          <h1 id="case-title" className="text-xl font-semibold">
            Case <span className="num text-base">{d.case.id.slice(0, 8)}</span>
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span data-testid="case-status">
              <StatusBadge status={d.case.status} />
            </span>
            <NbaChip
              action={d.case.nba_action}
              confidence={d.case.nba_confidence}
              fallback={d.case.fallback}
            />
            {d.case.requires_edit ? (
              <span className="text-sm text-amber">Needs an edit</span>
            ) : null}
          </div>
        </div>
        {awaiting && latest ? (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink"
          >
            Review draft v{latest.version}
          </button>
        ) : null}
      </header>

      {d.case.nba_rationale ? (
        <section aria-labelledby="why-title" className="rounded border border-rule bg-surface p-3">
          <h2 id="why-title" className="font-semibold">
            Why this action
          </h2>
          <p className="mt-1 text-sm">{d.case.nba_rationale}</p>
          {d.cited_notes.length ? (
            <ul className="mt-2 space-y-1 text-sm">
              {d.cited_notes.map((n) => (
                <li key={n.id} className="border-l-2 border-teal pl-2">
                  <span className="text-xs text-muted">{humanize(n.source)}: </span>
                  {n.body}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      <section aria-labelledby="progress-title">
        <h2 id="progress-title" className="font-semibold">
          Progress{" "}
          <span className="text-xs font-normal text-muted">
            {connected ? "live" : "reconnecting…"}
          </span>
        </h2>
        {d.steps.length === 0 && events.length === 0 ? (
          <Empty title="Waiting for the worker">
            The case starts within seconds of being queued.
          </Empty>
        ) : (
          <ol className="mt-2 space-y-1 text-sm" data-testid="progress">
            {d.steps
              .filter((s) => s.kind === "node")
              .map((s, i) => (
                <li key={`s${i}`} className="flex gap-2">
                  <span className="num w-40 text-muted">{formatDateTime(s.started_at)}</span>
                  <span>{humanize(s.node)}</span>
                  <span className="text-muted">{s.status}</span>
                </li>
              ))}
            {events
              .filter((e) => e.event === "node" && e.data.phase === "end")
              .map((e) => (
                <li key={e.id} className="flex gap-2">
                  <span className="w-40 text-muted">live</span>
                  <span>{humanize(String(e.data.node))}</span>
                  <span className="text-muted">{String(e.data.status)}</span>
                </li>
              ))}
          </ol>
        )}
      </section>

      <section aria-labelledby="drafts-title">
        <h2 id="drafts-title" className="font-semibold">
          Drafts
        </h2>
        {d.drafts.length === 0 ? (
          <Empty title="No draft">
            This action does not send an SMS, or the draft is still being written.
          </Empty>
        ) : (
          <ul className="mt-2 space-y-2">
            {[...d.drafts].reverse().map((draft) => (
              <li key={draft.version} className="rounded border border-rule bg-surface p-2 text-sm">
                <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  v{draft.version} · {humanize(draft.status)} · {humanize(draft.author)}
                  <SourceChip fallback={draft.author === "fallback"} />
                </p>
                <p className="mt-1 whitespace-pre-wrap">{draft.body}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {d.approvals.length ? (
        <section aria-labelledby="decisions-title">
          <h2 id="decisions-title" className="font-semibold">
            Decisions
          </h2>
          <ul className="mt-2 space-y-1 text-sm">
            {d.approvals.map((a, i) => (
              <li key={i}>
                v{a.draft_version} {humanize(a.decision)} by {a.decided_by} ·{" "}
                {formatDateTime(a.decided_at)}
                {a.reason ? ` · ${a.reason}` : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {open ? <ApprovalDrawer detail={d} onClose={() => setOpen(false)} /> : null}
    </article>
  );
}
