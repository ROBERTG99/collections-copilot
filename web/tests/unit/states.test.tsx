// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Empty, ErrorState, Skeleton } from "@/components/states";
import { ApiError } from "@/lib/api/client";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("Skeleton", () => {
  it("is a polite status region with the requested number of rows", () => {
    const { container } = render(<Skeleton rows={3} label="Loading queue" />);
    const status = screen.getByRole("status", { name: "Loading queue" });
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(3);
    expect(status.textContent).toContain("Loading queue…");
  });

  it("defaults to eight rows labelled Loading", () => {
    const { container } = render(<Skeleton />);
    expect(screen.getByRole("status", { name: "Loading" })).toBeTruthy();
    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(8);
  });
});

describe("Empty", () => {
  it("shows the title, the explanation and the next action", () => {
    render(
      <Empty title="Nothing to approve" action={<button type="button">Refresh</button>}>
        New drafts appear here.
      </Empty>,
    );
    expect(screen.getByText("Nothing to approve")).toBeTruthy();
    expect(screen.getByText("New drafts appear here.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy();
  });

  it("renders only the title when there is nothing else", () => {
    const { container } = render(<Empty title="No replies" />);
    expect(container.textContent).toBe("No replies");
  });
});

describe("ErrorState", () => {
  it("shows the problem title, status, detail and correlation id with a retry", () => {
    const onRetry = vi.fn();
    render(
      <ErrorState
        error={
          new ApiError({
            status: 409,
            title: "Conflict",
            detail: "The draft changed.",
            correlation_id: "corr-123",
          })
        }
        onRetry={onRetry}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Conflict (409)");
    expect(alert.textContent).toContain("The draft changed.");
    expect(alert.textContent).toContain("Correlation ID: corr-123");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("falls back to a generic title and the error message for non-API errors", () => {
    render(<ErrorState error={new Error("Failed to fetch")} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Something went wrong");
    expect(alert.textContent).toContain("Failed to fetch");
    expect(alert.textContent).not.toContain("(");
    expect(alert.textContent).not.toContain("Correlation ID");
    expect(screen.queryByRole("button")).toBeNull(); // no retry without a handler
  });

  it("shows no detail for a thrown non-Error value", () => {
    render(<ErrorState error="boom" />);
    expect(screen.getByRole("alert").textContent).toBe("Something went wrong");
  });

  it("offers Sign in again (back to the current page) instead of Retry on a 401", () => {
    window.history.replaceState(null, "", "/cases/abc?tab=drafts");
    render(
      <ErrorState
        error={new ApiError({ status: 401, title: "Unauthorized" })}
        onRetry={() => undefined}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Your session has ended (401)");
    const link = screen.getByRole("link", { name: "Sign in again" });
    expect(link.getAttribute("href")).toBe(
      `/auth/login?returnTo=${encodeURIComponent("/cases/abc?tab=drafts")}`,
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("lists the blocking violations of a refused post-check", () => {
    render(
      <ErrorState
        error={
          new ApiError({
            status: 422,
            title: "Post-check failed",
            postcheck: {
              violations: [
                { code: "quiet_hours", message: "Outside 08:00-21:00", blocking: true },
                { code: "tone_warning", message: "Sounds harsh", blocking: false },
                { code: "missing_opt_out", message: "Add STOP text" },
              ],
            },
          })
        }
      />,
    );
    const items = screen.getByTestId("problem-violations").querySelectorAll("li");
    expect(Array.from(items, (li) => li.textContent)).toEqual([
      "quiet hours: Outside 08:00-21:00",
      "missing opt out: Add STOP text",
    ]);
  });

  it("shows no violation list when the problem has none", () => {
    render(<ErrorState error={new ApiError({ status: 500, title: "Internal Server Error" })} />);
    expect(screen.queryByTestId("problem-violations")).toBeNull();
  });
});
