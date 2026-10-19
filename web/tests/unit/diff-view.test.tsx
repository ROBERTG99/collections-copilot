// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DiffView } from "@/components/approval-drawer/diff-view";

afterEach(cleanup);

describe("DiffView", () => {
  it("says so when the text is unchanged", () => {
    render(<DiffView before="Hi Ana, invoice 1042 is due." after="Hi Ana, invoice 1042 is due." />);
    expect(screen.getByText("No changes from the draft.")).toBeTruthy();
    expect(screen.queryByTestId("diff")).toBeNull();
  });

  it("marks added and removed words, announced for screen readers", () => {
    render(<DiffView before="Please pay today." after="Please pay by Friday." />);
    const diff = screen.getByTestId("diff");
    const added = Array.from(diff.querySelectorAll("ins"), (n) => n.textContent);
    const removed = Array.from(diff.querySelectorAll("del"), (n) => n.textContent);
    expect(added.join("")).toContain("added: ");
    expect(added.join("")).toContain("Friday");
    expect(removed.join("")).toContain("removed: ");
    expect(removed.join("")).toContain("today");
    // The unchanged words stay as plain text.
    expect(diff.textContent?.startsWith("Please pay")).toBe(true);
  });

  it("shows a pure insertion without any removal", () => {
    render(<DiffView before="Thanks." after="Thanks. Reply STOP to opt out." />);
    const diff = screen.getByTestId("diff");
    expect(diff.querySelectorAll("del")).toHaveLength(0);
    expect(diff.querySelector("ins")?.textContent).toContain("STOP");
  });
});
