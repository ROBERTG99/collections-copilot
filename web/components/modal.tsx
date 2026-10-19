"use client";

import { type ReactNode, useEffect, useId, useRef } from "react";

/**
 * A modal `<dialog>` (focus stays inside, the page behind is inert, Escape closes) that returns
 * focus to the control that opened it.
 */
export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (el && !el.open) el.showModal();
    return () => {
      el?.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className="m-auto max-h-[90dvh] w-full max-w-2xl overflow-y-auto rounded border-0 bg-bg p-4 text-ink shadow-[var(--cc-shadow)] backdrop:bg-scrim"
    >
      <div className="flex items-start justify-between gap-2">
        <h2 id={titleId} className="text-lg font-semibold">
          {title}
        </h2>
        <button type="button" onClick={onClose} className="rounded border border-rule px-2 py-1">
          Close
        </button>
      </div>
      <div className="mt-3">{children}</div>
    </dialog>
  );
}
