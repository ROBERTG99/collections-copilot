"use client";

import { useEffect, useRef, useState } from "react";

import { Confidence, type Source, SourceChip } from "@/components/chips";
import { ErrorState } from "@/components/states";
import { type CheckResult, postcheck, useDecision } from "@/lib/api/hooks";
import type { CaseDetail, DraftOut } from "@/lib/api/types";
import { formatDateTime, humanize } from "@/lib/format";

import { DiffView } from "./diff-view";

const MAX_SMS = 320;
const AUTHOR_SOURCE: Record<DraftOut["author"], Source> = {
  ai: "ai",
  analyst: "analyst",
  fallback: "fallback",
};

/** The result of one post-check, tied to the exact text it checked. */
type Check = { text: string; result: CheckResult | null; error: unknown };

/**
 * The approval drawer shows exactly what will be sent and written: the SMS text, the recipient,
 * the send time from the live post-check (in the debtor's time zone), and the side effects.
 * Edits are re-checked as you type; approval is enabled only once the text on screen has passed
 * (the API re-checks and refuses anyway). It is a modal `<dialog>`: focus stays inside, the page
 * behind is inert, and focus returns to the control that opened it.
 */
export function ApprovalDrawer({ detail, onClose }: { detail: CaseDetail; onClose: () => void }) {
  const latest = detail.drafts[detail.drafts.length - 1];
  const caseId = detail.case.id;
  const timeZone = detail.account.timezone;
  const [reviewedVersion, setReviewedVersion] = useState(latest?.version ?? 0);
  const draft = detail.drafts.find((d) => d.version === reviewedVersion) ?? latest;
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft?.body ?? "");
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [check, setCheck] = useState<Check | null>(null);
  const [attempt, setAttempt] = useState(0);
  const decide = useDecision(caseId);
  const dialog = useRef<HTMLDialogElement>(null);

  const current = text.trim();
  const edited = !!draft && current !== draft.body.trim();

  useEffect(() => {
    const el = dialog.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (el && !el.open) el.showModal();
    return () => {
      el?.close();
      const target = opener?.isConnected ? opener : document.getElementById("case-title");
      target?.focus();
    };
  }, []);

  // Live post-check on the text that would be sent (debounced while typing).
  useEffect(() => {
    if (!draft) return;
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const result = await postcheck(caseId, current || " ");
        if (!cancelled) setCheck({ text: current, result, error: null });
      } catch (error) {
        if (!cancelled) setCheck({ text: current, result: null, error });
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [caseId, draft, current, attempt]);

  if (!draft) return null;
  const fresh = check?.text === current ? check : null; // a check of other text says nothing
  const result = fresh?.result ?? null;
  const checking = fresh == null;
  const blocking = result?.violations.filter((v) => v.blocking) ?? [];
  const tooLong = current.length > MAX_SMS;
  // A newer draft from elsewhere (the worker re-drafted); never the edit just recorded here.
  const newer =
    latest && latest.version !== draft.version && !decide.isSuccess && !decide.isPending
      ? latest
      : null;
  const canApprove =
    detail.case.status === "awaiting_approval" &&
    !newer &&
    !!result?.ok &&
    !tooLong &&
    current.length > 0 &&
    !decide.isPending;

  function requestClose() {
    if (edited && !decide.isSuccess && !window.confirm("Discard your edit to this draft?")) return;
    onClose();
  }

  return (
    <dialog
      ref={dialog}
      aria-labelledby="drawer-title"
      onCancel={(e) => {
        e.preventDefault(); // Escape: same path as Close, so an edit is never lost silently
        requestClose();
      }}
      onClick={(e) => {
        if (e.target === dialog.current) requestClose(); // a click on the scrim
      }}
      className="fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-dvh w-full max-w-xl overflow-y-auto border-0 bg-bg p-4 text-ink shadow-[var(--cc-shadow)] backdrop:bg-scrim"
    >
      <div className="flex items-start justify-between gap-2">
        <h2 id="drawer-title" className="text-lg font-semibold">
          Review SMS draft v{draft.version}
        </h2>
        <button
          type="button"
          onClick={requestClose}
          className="rounded border border-rule px-2 py-1"
        >
          Close
        </button>
      </div>

      {newer ? (
        <div role="status" className="mt-3 rounded border border-amber p-2 text-sm">
          Draft v{newer.version} replaced the one you are reviewing.{" "}
          <button
            type="button"
            className="underline"
            onClick={() => {
              setReviewedVersion(newer.version);
              setText(newer.body);
              setEditing(false);
            }}
          >
            Review v{newer.version}
          </button>
        </div>
      ) : null}

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted">To</dt>
        <dd className="num" data-testid="recipient">
          {detail.recipient_e164}
        </dd>
        <dt className="text-muted">Sends</dt>
        <dd data-testid="send-time">
          {result ? (
            <>
              {formatDateTime(result.send_at, { timeZone })} debtor time
              {result.deferred
                ? " (next 08:00 in the debtor's time zone)"
                : " (as soon as approved)"}
              <span className="block text-xs text-muted">
                {formatDateTime(result.send_at)} your time
              </span>
            </>
          ) : (
            "Not known until the post-check passes"
          )}
        </dd>
        <dt className="text-muted">Writes</dt>
        <dd>
          One outbound SMS and one contact-log entry (counts toward 3 per 7 days). No ledger write:
          promises are recorded only from the debtor&apos;s reply.
        </dd>
        <dt className="text-muted">Source</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <SourceChip source={edited ? "analyst" : AUTHOR_SOURCE[draft.author]} />
          <span className="text-xs text-muted">
            next best action {humanize(detail.case.nba_action)}{" "}
            <Confidence value={detail.case.nba_confidence} />
          </span>
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
        aria-describedby="sms-count"
        className="mt-1 w-full rounded border border-rule bg-surface p-2"
      />
      <p id="sms-count" className={`num text-xs ${tooLong ? "text-danger" : "text-muted"}`}>
        {current.length} / {MAX_SMS} characters
        {tooLong ? ` · ${current.length - MAX_SMS} over the SMS limit` : ""}
      </p>

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
        {checking ? (
          <p className="text-sm text-muted">Checking this text…</p>
        ) : fresh?.error ? (
          <ErrorState error={fresh.error} onRetry={() => setAttempt((n) => n + 1)} />
        ) : result?.ok ? (
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

      {decide.isError ? (
        <ErrorState
          error={decide.error}
          onRetry={decide.variables ? () => decide.mutate(decide.variables) : undefined}
        />
      ) : null}
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
                ? { decision: "edit", draft_version: draft.version, body: current }
                : { decision: "approve", draft_version: draft.version },
            )
          }
          className="rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink disabled:opacity-50"
        >
          {decide.isPending ? "Recording…" : edited ? "Save edit and approve" : "Approve"}
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
          aria-expanded={rejecting}
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
    </dialog>
  );
}
