"use client";

import { useEffect, useRef, useState } from "react";

import { SourceChip } from "@/components/chips";
import { ErrorState } from "@/components/states";
import { type CheckResult, postcheck, useDecision } from "@/lib/api/hooks";
import type { CaseDetail } from "@/lib/api/types";
import { formatDateTime, humanize } from "@/lib/format";

import { DiffView } from "./diff-view";

/**
 * The approval drawer shows exactly what will be sent and written: the SMS text, the recipient,
 * the send time from the live post-check, and the side effects. Edits are re-checked as you type;
 * a failing post-check disables approval (the API re-checks and refuses anyway).
 */
export function ApprovalDrawer({ detail, onClose }: { detail: CaseDetail; onClose: () => void }) {
  const draft = detail.drafts[detail.drafts.length - 1];
  const caseId = detail.case.id;
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft?.body ?? "");
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [check, setCheck] = useState<CheckResult | null>(
    (draft?.postcheck as CheckResult | null | undefined) ?? null,
  );
  const [checking, setChecking] = useState(false);
  const decide = useDecision(caseId);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    dialog.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Live post-check on the text that would be sent (debounced while typing).
  useEffect(() => {
    if (!draft) return;
    let cancelled = false;
    const handle = setTimeout(async () => {
      setChecking(true);
      try {
        const result = await postcheck(caseId, text.trim() || " ");
        if (!cancelled) setCheck(result);
      } catch {
        if (!cancelled) setCheck(null);
      } finally {
        if (!cancelled) setChecking(false);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [caseId, draft, text]);

  if (!draft) return null;
  const edited = text.trim() !== draft.body.trim();
  const blocking = check?.violations.filter((v) => v.blocking) ?? [];
  const canApprove =
    detail.case.status === "awaiting_approval" &&
    !!check?.ok &&
    !checking &&
    text.trim().length > 0 &&
    !decide.isPending;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose}>
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full max-w-xl overflow-y-auto bg-bg p-4 shadow-xl"
      >
        <div className="flex items-start justify-between gap-2">
          <h2 id="drawer-title" className="text-lg font-semibold">
            Review SMS draft v{draft.version}
          </h2>
          <button type="button" onClick={onClose} className="rounded border border-rule px-2 py-1">
            Close
          </button>
        </div>

        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-muted">To</dt>
          <dd className="num" data-testid="recipient">
            {detail.recipient_e164}
          </dd>
          <dt className="text-muted">Sends</dt>
          <dd data-testid="send-time">
            {check ? formatDateTime(check.send_at) : "—"}
            {check?.deferred ? " (next 08:00 in the debtor's time zone)" : " (immediately)"}
          </dd>
          <dt className="text-muted">Writes</dt>
          <dd>
            One outbound SMS and one contact-log entry (counts toward 3 per 7 days). No ledger
            write: promises are recorded only from the debtor&apos;s reply.
          </dd>
          <dt className="text-muted">Source</dt>
          <dd>
            <SourceChip fallback={draft.author === "fallback"} />{" "}
            {draft.model ? <span className="text-xs text-muted">{draft.model}</span> : null}
          </dd>
        </dl>

        <label htmlFor="sms-body" className="mt-4 block font-semibold">
          Message {editing ? "(editing)" : "(exactly as sent)"}
        </label>
        <textarea
          id="sms-body"
          value={text}
          readOnly={!editing}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          maxLength={1000}
          className="mt-1 w-full rounded border border-rule bg-surface p-2"
        />
        <p className="num text-xs text-muted">{text.trim().length} / 320 characters</p>

        {edited ? (
          <section aria-label="Changes from the draft" className="mt-2">
            <h3 className="text-sm font-semibold">Changes</h3>
            <DiffView before={draft.body} after={text} />
          </section>
        ) : null}

        <section aria-live="polite" aria-label="Compliance post-check" className="mt-3">
          <h3 className="text-sm font-semibold">
            Compliance post-check {checking ? "(checking…)" : ""}
          </h3>
          {check == null ? (
            <p className="text-sm text-muted">Not checked yet.</p>
          ) : check.ok ? (
            <p className="text-sm text-teal" data-testid="postcheck-ok">
              Passes every rule.
            </p>
          ) : (
            <ul className="list-disc pl-5 text-sm text-danger" data-testid="postcheck-violations">
              {blocking.map((v) => (
                <li key={v.code}>
                  <span className="font-semibold">{humanize(v.code)}</span>: {v.message}
                </li>
              ))}
            </ul>
          )}
        </section>

        {decide.isError ? <ErrorState error={decide.error} /> : null}
        {decide.isSuccess ? (
          <p role="status" className="mt-3 text-sm text-teal">
            Decision recorded. The worker re-checks before sending.
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={!canApprove}
            onClick={() =>
              decide.mutate(
                edited
                  ? { decision: "edit", draft_version: draft.version, body: text.trim() }
                  : { decision: "approve", draft_version: draft.version },
              )
            }
            className="rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink disabled:opacity-50"
          >
            {edited ? "Save edit and approve" : "Approve"}
          </button>
          {!editing ? (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded border border-rule px-3 py-1.5"
            >
              Edit
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setRejecting((r) => !r)}
            className="rounded border border-danger px-3 py-1.5 text-danger"
          >
            Reject
          </button>
        </div>
        {rejecting ? (
          <form
            className="mt-3 flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              decide.mutate({
                decision: "reject",
                draft_version: draft.version,
                reason: reason.trim(),
              });
            }}
          >
            <label htmlFor="reject-reason" className="text-sm">
              Reason (required, kept in the audit log)
            </label>
            <input
              id="reject-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              className="rounded border border-rule bg-surface px-2 py-1"
            />
            <button
              type="submit"
              disabled={!reason.trim() || decide.isPending}
              className="self-start rounded bg-danger px-3 py-1.5 font-semibold text-teal-ink disabled:opacity-50"
            >
              Reject draft
            </button>
          </form>
        ) : null}
      </div>
    </div>
  );
}
