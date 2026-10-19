"use client";

import { useEffect } from "react";

/** A render error in a console screen: say so, give support a reference, offer a retry. */
export default function ConsoleError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    document.title = "Something went wrong · collections-copilot";
  }, []);
  return (
    <div role="alert" className="rounded border border-danger p-4">
      <h1 className="font-semibold text-danger">This screen could not be shown</h1>
      <p className="mt-1">{error.message || "An unexpected error occurred."}</p>
      {error.digest ? (
        <p className="mt-1 text-sm text-muted">
          Reference: <span className="num">{error.digest}</span>
        </p>
      ) : null}
      <button
        type="button"
        onClick={reset}
        className="mt-3 rounded border border-rule px-3 py-1 hover:bg-surface-2"
      >
        Retry
      </button>
    </div>
  );
}
