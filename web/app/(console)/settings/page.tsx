"use client";

import { useState } from "react";

import { Empty, ErrorState, Skeleton } from "@/components/states";
import { useHasRole } from "@/components/user-context";
import { useAddOptOut, useAutoApproval, useOptOuts, useSaveAutoApproval } from "@/lib/api/hooks";
import { formatDateTime } from "@/lib/format";

export default function SettingsPage() {
  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold">Settings</h1>
      <AutoApproval />
      <OptOuts />
    </div>
  );
}

function AutoApproval() {
  const current = useAutoApproval();
  return (
    <section aria-labelledby="auto-title" className="rounded border border-rule bg-surface p-4">
      <h2 id="auto-title" className="font-semibold">
        Auto-approval
      </h2>
      <p className="text-sm text-muted">
        Only reminders, never payment plans. Settings can only tighten the platform ceiling.
      </p>
      {current.isPending ? (
        <Skeleton rows={3} label="Loading auto-approval" />
      ) : current.isError ? (
        <ErrorState error={current.error} onRetry={() => void current.refetch()} />
      ) : (
        <AutoApprovalForm key={JSON.stringify(current.data)} current={current.data} />
      )}
    </section>
  );
}

type AutoApprovalSettings = {
  enabled: boolean;
  max_usd: number;
  min_confidence: number;
  ceiling_max_usd: number;
  floor_min_confidence: number;
};

function AutoApprovalForm({ current }: { current: AutoApprovalSettings }) {
  const save = useSaveAutoApproval();
  const supervisor = useHasRole("supervisor", "admin");
  const [form, setForm] = useState({
    enabled: current.enabled,
    max_usd: current.max_usd,
    min_confidence: current.min_confidence,
  });
  return (
    <form
      className="mt-3 grid max-w-md gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(form);
      }}
    >
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={form.enabled}
          disabled={!supervisor}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
        />
        Auto-approve small, confident reminders
      </label>
      <label className="flex flex-col text-sm">
        Maximum open balance (USD, ceiling {current.ceiling_max_usd})
        <input
          type="number"
          min={0}
          max={current.ceiling_max_usd}
          step="1"
          value={form.max_usd}
          disabled={!supervisor}
          onChange={(e) => setForm({ ...form, max_usd: Number(e.target.value) })}
          className="num mt-1 rounded border border-rule bg-surface px-2 py-1"
        />
      </label>
      <label className="flex flex-col text-sm">
        Minimum model confidence (floor {current.floor_min_confidence})
        <input
          type="number"
          min={current.floor_min_confidence}
          max={1}
          step="0.01"
          value={form.min_confidence}
          disabled={!supervisor}
          onChange={(e) => setForm({ ...form, min_confidence: Number(e.target.value) })}
          className="num mt-1 rounded border border-rule bg-surface px-2 py-1"
        />
      </label>
      {supervisor ? (
        <button
          type="submit"
          disabled={save.isPending}
          className="justify-self-start rounded bg-teal px-3 py-1.5 font-semibold text-teal-ink"
        >
          Save
        </button>
      ) : (
        <p className="text-sm text-muted">Only supervisors can change auto-approval.</p>
      )}
      {save.isError ? <ErrorState error={save.error} /> : null}
      {save.isSuccess ? (
        <p role="status" className="text-sm text-teal">
          Saved.
        </p>
      ) : null}
    </form>
  );
}

function OptOuts() {
  const list = useOptOuts();
  const add = useAddOptOut();
  const [phone, setPhone] = useState("");
  return (
    <section aria-labelledby="optouts-title" className="rounded border border-rule bg-surface p-4">
      <h2 id="optouts-title" className="font-semibold">
        Opt-outs
      </h2>
      <form
        className="mt-3 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate(phone.trim(), { onSuccess: () => setPhone("") });
        }}
      >
        <label className="flex flex-col text-sm">
          Phone number (E.164)
          <input
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+15550001234"
            className="num mt-1 rounded border border-rule bg-surface px-2 py-1"
          />
        </label>
        <button
          type="submit"
          disabled={!phone.trim() || add.isPending}
          className="rounded border border-rule px-3 py-1"
        >
          Add opt-out
        </button>
      </form>
      {add.isError ? <ErrorState error={add.error} /> : null}
      <div className="mt-3">
        {list.isPending ? (
          <Skeleton rows={4} label="Loading opt-outs" />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.data.length === 0 ? (
          <Empty title="No opt-outs" />
        ) : (
          <ul className="space-y-1 text-sm" data-testid="opt-outs">
            {list.data.map((o) => (
              <li key={o.phone_e164}>
                <span className="num">{o.phone_e164}</span> · {o.source} ·{" "}
                {formatDateTime(o.created_at)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
