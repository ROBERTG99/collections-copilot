// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ComplianceBadge, Confidence, NbaChip, SourceChip, StatusBadge } from "@/components/chips";

afterEach(cleanup);

describe("SourceChip (AI UX: who produced a value)", () => {
  it.each([
    [{ source: "ai" as const }, "AI draft", "Suggested by a model; a person approves"],
    [{ source: "model" as const }, "AI draft", "Suggested by a model; a person approves"],
    [{ source: "fallback" as const }, "Template", "Rules or template, no model"],
    [{ source: "analyst" as const }, "Edited by analyst", "Written or corrected by a person"],
    [{ source: "rule" as const }, "Rule", "Decided by a deterministic rule"],
  ])("labels %j as %s", (props, label, title) => {
    render(<SourceChip {...props} />);
    expect(screen.getByText(label).getAttribute("title")).toBe(title);
  });

  it("says when the source was not recorded", () => {
    render(<SourceChip source="unknown" />);
    expect(screen.getByText("Source not recorded")).toBeTruthy();
  });

  it("defaults to AI draft, or Template when the value came from a fallback", () => {
    const { rerender } = render(<SourceChip />);
    expect(screen.getByText("AI draft")).toBeTruthy();
    rerender(<SourceChip fallback />);
    expect(screen.getByText("Template")).toBeTruthy();
    expect(screen.queryByText("AI draft")).toBeNull();
  });

  it("lets an explicit source win over the fallback flag", () => {
    render(<SourceChip source="analyst" fallback />);
    expect(screen.getByText("Edited by analyst")).toBeTruthy();
  });
});

describe("Confidence", () => {
  it("reads out a percentage with its meaning, or a dash when unknown", () => {
    const { container, rerender } = render(<Confidence value={0.826} />);
    expect(container.textContent).toBe("confidence 83%");
    rerender(<Confidence value={null} />);
    expect(container.textContent).toBe("confidence —");
  });
});

describe("NbaChip", () => {
  it("shows the humanized action, its confidence and the AI label", () => {
    const { container } = render(<NbaChip action="send_sms" confidence={0.9} fallback={false} />);
    expect(screen.getByText("send sms")).toBeTruthy();
    expect(container.textContent).toContain("confidence 90%");
    expect(screen.getByText("AI draft")).toBeTruthy();
  });

  it("marks a fallback recommendation as Template", () => {
    render(<NbaChip action="call_debtor" confidence={null} fallback />);
    expect(screen.getByText("Template")).toBeTruthy();
    expect(screen.queryByText("AI draft")).toBeNull();
  });

  it("shows a dash when there is no action", () => {
    const { container } = render(<NbaChip action={null} confidence={0.5} fallback={false} />);
    expect(container.textContent).toBe("—");
  });
});

describe("ComplianceBadge", () => {
  it("is Clear when the pre-check passed", () => {
    render(<ComplianceBadge ok codes={[]} />);
    expect(screen.getByText("Clear")).toBeTruthy();
  });

  it("names every blocking rule, humanized, with the raw codes as the title", () => {
    render(<ComplianceBadge ok={false} codes={["quiet_hours", "contact_cap"]} />);
    const badge = screen.getByText("Blocked: quiet hours, contact cap");
    expect(badge.getAttribute("title")).toBe("quiet_hours, contact_cap");
  });

  it("says not checked when there is no result", () => {
    render(<ComplianceBadge ok={undefined} codes={[]} />);
    expect(screen.getByText("not checked")).toBeTruthy();
  });
});

describe("StatusBadge", () => {
  it("humanizes the case status and covers accounts without a case", () => {
    const { rerender } = render(<StatusBadge status="awaiting_approval" />);
    expect(screen.getByText("awaiting approval")).toBeTruthy();
    rerender(<StatusBadge status={null} />);
    expect(screen.getByText("no case")).toBeTruthy();
  });
});
