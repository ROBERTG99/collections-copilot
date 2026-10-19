// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalDrawer } from "@/components/approval-drawer/approval-drawer";
import { ApiError } from "@/lib/api/client";
import { type CheckResult, postcheck, useDecision } from "@/lib/api/hooks";
import type { CaseDetail, DraftOut } from "@/lib/api/types";

vi.mock("@/lib/api/hooks", () => ({ postcheck: vi.fn(), useDecision: vi.fn() }));

// jsdom implements `open` but not showModal()/close(); model them on the attribute.
const proto = HTMLDialogElement.prototype as Partial<HTMLDialogElement>;
const original = { showModal: proto.showModal, close: proto.close };
beforeAll(() => {
  proto.showModal = function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  proto.close = function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});
afterAll(() => {
  proto.showModal = original.showModal;
  proto.close = original.close;
});

const BODY = "Hi Ana, invoice INV-1042 for $1,250.00 is 30 days past due. Reply STOP to opt out.";
const SEND_AT = "2026-10-19T15:00:00Z"; // 08:00 PDT for the debtor, 11:00 EDT for the analyst

function draft(overrides: Partial<DraftOut> = {}): DraftOut {
  return {
    author: "ai",
    body: BODY,
    created_at: "2026-10-18T12:00:00Z",
    language: "en",
    model: "fake-fast-1",
    postcheck: null,
    prompt_version: "sms_draft.v1",
    status: "pending",
    version: 1,
    ...overrides,
  };
}

function detail(drafts: DraftOut[], status = "awaiting_approval"): CaseDetail {
  return {
    account: {
      account_id: "acc-1",
      case_id: "case-1",
      case_status: status,
      compliance_codes: [],
      compliance_ok: true,
      contact_flag: null,
      external_ref: "ACME-001",
      fallback: false,
      language: "en",
      max_days_past_due: 30,
      name: "Acme Widgets",
      nba_action: "send_sms",
      nba_confidence: 0.82,
      open_balance: { amount_cents: 125_000, currency: "USD" },
      opted_out: false,
      priority_score: 71,
      score_components: null,
      segment: "smb",
      timezone: "America/Los_Angeles",
    },
    approvals: [],
    case: {
      analyst_reason: null,
      cited_note_ids: [],
      closed_at: null,
      created_at: "2026-10-18T11:00:00Z",
      fallback: false,
      id: "case-1",
      nba_action: "send_sms",
      nba_confidence: 0.82,
      nba_rationale: null,
      priority_score: 71,
      requires_edit: false,
      status,
    },
    cited_notes: [],
    drafts,
    messages: [],
    precheck: null,
    recipient_e164: "+15555550123",
    steps: [],
  };
}

const OK: CheckResult = { ok: true, deferred: true, send_at: SEND_AT, violations: [] };
const REFUSED: CheckResult = {
  ok: false,
  deferred: false,
  send_at: SEND_AT,
  violations: [
    { code: "prohibited_language", message: "Do not promise outcomes", blocking: true },
    { code: "tone_warning", message: "Sounds harsh", blocking: false },
  ],
};

type Decide = ReturnType<typeof useDecision>;
const mutate = vi.fn();
function decideState(overrides: Partial<Decide> = {}) {
  vi.mocked(useDecision).mockReturnValue({
    mutate,
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
    variables: undefined,
    ...overrides,
  } as unknown as Decide);
}

beforeEach(() => {
  vi.mocked(postcheck).mockImplementation(async (_caseId, body) =>
    body.includes("guaranteed") ? REFUSED : OK,
  );
  decideState();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const approveButton = () =>
  screen.getByRole("button", { name: /^(Approve|Save edit and approve|Recording…)$/ });
const textarea = () => screen.getByLabelText(/^Message/) as HTMLTextAreaElement;

function edit(text: string) {
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(textarea(), { target: { value: text } });
}

describe("ApprovalDrawer: what will be sent", () => {
  it("shows the recipient, exact text, source and side effects, then the checked send time", async () => {
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    const dialog = screen.getByRole("dialog", { name: "Review SMS draft v1" });
    expect((dialog as HTMLDialogElement).open).toBe(true);
    expect(screen.getByTestId("recipient").textContent).toBe("+15555550123");
    expect(textarea().value).toBe(BODY);
    expect(textarea().readOnly).toBe(true);
    expect(screen.getByText("Message (exactly as sent)")).toBeTruthy();
    expect(screen.getByText(`${BODY.length} / 320 characters`)).toBeTruthy();
    expect(screen.getByText("AI draft")).toBeTruthy();
    expect(screen.getByText("fake-fast-1")).toBeTruthy();
    expect(dialog.textContent).toContain("next best action send sms");
    expect(dialog.textContent).toContain("confidence 82%");
    expect(dialog.textContent).toContain("No ledger write");

    // Until the post-check of this text returns, nothing can be approved.
    expect(screen.getByTestId("send-time").textContent).toBe(
      "Not known until the post-check passes",
    );
    expect(screen.getByText("Checking this text…")).toBeTruthy();
    expect((approveButton() as HTMLButtonElement).disabled).toBe(true);

    expect((await screen.findByTestId("postcheck-ok")).textContent).toBe("Passes every rule.");
    expect(postcheck).toHaveBeenCalledWith("case-1", BODY);
    const sends = screen.getByTestId("send-time").textContent ?? "";
    expect(sends).toMatch(/Oct 19, 2026, 8:00\sAM PDT debtor time/);
    expect(sends).toContain("(next 08:00 in the debtor's time zone)");
    expect(sends).toMatch(/Oct 19, 2026, 11:00\sAM your time/);
    expect((approveButton() as HTMLButtonElement).disabled).toBe(false);
  });

  it("says a passing message goes out as soon as it is approved when not deferred", async () => {
    vi.mocked(postcheck).mockResolvedValue({ ...OK, deferred: false });
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    await screen.findByTestId("postcheck-ok");
    expect(screen.getByTestId("send-time").textContent).toContain("(as soon as approved)");
  });

  it("labels a template fallback draft as Template", async () => {
    render(
      <ApprovalDrawer
        detail={detail([draft({ author: "fallback", model: null })])}
        onClose={() => undefined}
      />,
    );
    expect(screen.getByText("Template")).toBeTruthy();
    expect(screen.queryByText("AI draft")).toBeNull();
    expect(screen.queryByText("fake-fast-1")).toBeNull();
    await screen.findByTestId("postcheck-ok");
  });

  it("renders nothing and checks nothing without a draft", () => {
    vi.useFakeTimers();
    const { container } = render(<ApprovalDrawer detail={detail([])} onClose={() => undefined} />);
    vi.advanceTimersByTime(1000);
    expect(container.innerHTML).toBe("");
    expect(postcheck).not.toHaveBeenCalled();
  });
});

describe("ApprovalDrawer: approve", () => {
  it("approves the reviewed version once the post-check passes", async () => {
    render(<ApprovalDrawer detail={detail([draft({ version: 3 })])} onClose={() => undefined} />);
    await screen.findByTestId("postcheck-ok");
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(mutate).toHaveBeenCalledWith({ decision: "approve", draft_version: 3 });
  });

  it("cannot approve a case that is no longer awaiting approval", async () => {
    render(<ApprovalDrawer detail={detail([draft()], "queued")} onClose={() => undefined} />);
    await screen.findByTestId("postcheck-ok");
    expect((approveButton() as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows Recording… and disables the decision buttons while a decision is pending", async () => {
    decideState({ isPending: true });
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    await screen.findByTestId("postcheck-ok");
    const button = screen.getByRole("button", { name: "Recording…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.change(screen.getByLabelText(/^Reason/), { target: { value: "Wrong amount" } });
    expect(
      (screen.getByRole("button", { name: "Reject draft" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("confirms a recorded decision", async () => {
    decideState({ isSuccess: true });
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    expect(screen.getByRole("status").textContent).toBe(
      "Decision recorded. The worker re-checks before sending.",
    );
    await screen.findByTestId("postcheck-ok");
  });

  it("shows a refused decision with its problem and retries the same decision", async () => {
    const variables = { decision: "approve" as const, draft_version: 1 };
    decideState({
      isError: true,
      error: new ApiError({ status: 409, title: "Conflict", detail: "A newer draft exists." }),
      variables,
    } as Partial<Decide>);
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    await screen.findByTestId("postcheck-ok");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Conflict (409)");
    expect(alert.textContent).toContain("A newer draft exists.");
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(mutate).toHaveBeenCalledWith(variables);
  });
});

describe("ApprovalDrawer: edit", () => {
  it("re-checks the edited text, shows the diff, and blocks approval on violations", async () => {
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    await screen.findByTestId("postcheck-ok");

    edit("Hi Ana, payment is guaranteed to clear your record.");
    expect(screen.getByText("Message (editing)")).toBeTruthy();
    expect(textarea().readOnly).toBe(false);
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.getByText("Edited by analyst")).toBeTruthy();
    expect(screen.queryByText("AI draft")).toBeNull();
    const changes = screen.getByRole("region", { name: "Changes from the draft" });
    const added = within(changes).getByTestId("diff").querySelectorAll("ins");
    expect(Array.from(added, (n) => n.textContent).join(" ")).toContain("guaranteed");
    // The earlier pass was for other text: it says nothing about this one.
    expect(screen.getByText("Checking this text…")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save edit and approve" })).toBeTruthy();

    const violations = await screen.findByTestId("postcheck-violations");
    expect(Array.from(violations.querySelectorAll("li"), (li) => li.textContent)).toEqual([
      "prohibited language: Do not promise outcomes",
    ]);
    expect(postcheck).toHaveBeenLastCalledWith(
      "case-1",
      "Hi Ana, payment is guaranteed to clear your record.",
    );
    expect((approveButton() as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("send-time").textContent).toContain("debtor time");
  });

  it("saves a corrected edit, trimmed, as an edit decision", async () => {
    render(<ApprovalDrawer detail={detail([draft({ version: 2 })])} onClose={() => undefined} />);
    await screen.findByTestId("postcheck-ok");
    edit("  Hi Ana, please call us about INV-1042. Reply STOP to opt out.  ");
    await screen.findByTestId("postcheck-ok");
    expect(postcheck).toHaveBeenLastCalledWith(
      "case-1",
      "Hi Ana, please call us about INV-1042. Reply STOP to opt out.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save edit and approve" }));
    expect(mutate).toHaveBeenCalledWith({
      decision: "edit",
      draft_version: 2,
      body: "Hi Ana, please call us about INV-1042. Reply STOP to opt out.",
    });
  });

  it("refuses text over the SMS limit even when the rules pass", async () => {
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    edit("x".repeat(321));
    expect(screen.getByText("321 / 320 characters · 1 over the SMS limit")).toBeTruthy();
    await screen.findByTestId("postcheck-ok");
    expect((approveButton() as HTMLButtonElement).disabled).toBe(true);
  });

  it("never approves empty text", async () => {
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    edit("   ");
    await screen.findByTestId("postcheck-ok");
    expect(postcheck).toHaveBeenLastCalledWith("case-1", " ");
    expect((approveButton() as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("ApprovalDrawer: post-check errors", () => {
  it("shows the post-check problem with a retry that checks again", async () => {
    vi.mocked(postcheck).mockRejectedValueOnce(
      new ApiError({ status: 503, title: "Service Unavailable", correlation_id: "corr-77" }),
    );
    render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Service Unavailable (503)");
    expect(alert.textContent).toContain("Correlation ID: corr-77");
    expect((approveButton() as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    await screen.findByTestId("postcheck-ok");
    expect(postcheck).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("ApprovalDrawer: reject", () => {
  it("requires a reason and records it trimmed", async () => {
    render(<ApprovalDrawer detail={detail([draft({ version: 4 })])} onClose={() => undefined} />);
    const toggle = screen.getByRole("button", { name: "Reject" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    const submit = screen.getByRole("button", { name: "Reject draft" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    const reason = screen.getByLabelText("Reason (required, kept in the audit log)");
    fireEvent.change(reason, { target: { value: "   " } });
    expect(submit.disabled).toBe(true);
    fireEvent.change(reason, { target: { value: "  Debtor disputes the amount  " } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    expect(mutate).toHaveBeenCalledWith({
      decision: "reject",
      draft_version: 4,
      reason: "Debtor disputes the amount",
    });

    fireEvent.click(toggle);
    expect(screen.queryByRole("button", { name: "Reject draft" })).toBeNull();
    await screen.findByTestId("postcheck-ok");
  });
});

describe("ApprovalDrawer: a newer draft arrives", () => {
  it("warns, blocks approval of the stale draft, and switches to the new one", async () => {
    const v1 = draft({ version: 1 });
    const v2 = draft({ version: 2, body: "Hi Ana, a gentle reminder about INV-1042." });
    const { rerender } = render(<ApprovalDrawer detail={detail([v1])} onClose={() => undefined} />);
    await screen.findByTestId("postcheck-ok");

    rerender(<ApprovalDrawer detail={detail([v1, v2])} onClose={() => undefined} />);
    expect(screen.getByText(/Draft v2 replaced the one you are reviewing\./)).toBeTruthy();
    await screen.findByTestId("postcheck-ok");
    expect((approveButton() as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Review v2" }));
    expect(screen.getByRole("dialog", { name: "Review SMS draft v2" })).toBeTruthy();
    expect(textarea().value).toBe(v2.body);
    expect(screen.queryByText(/replaced the one you are reviewing/)).toBeNull();
    await waitFor(() => expect(postcheck).toHaveBeenLastCalledWith("case-1", v2.body));
    await screen.findByTestId("postcheck-ok");
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(mutate).toHaveBeenCalledWith({ decision: "approve", draft_version: 2 });
  });
});

describe("ApprovalDrawer: closing", () => {
  it("closes without asking when nothing was edited (button, Escape, scrim)", () => {
    const confirm = vi.spyOn(window, "confirm");
    const onClose = vi.fn();
    render(<ApprovalDrawer detail={detail([draft()])} onClose={onClose} />);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    const cancel = new Event("cancel", { cancelable: true });
    fireEvent(dialog, cancel);
    expect(cancel.defaultPrevented).toBe(true);
    fireEvent.click(dialog); // the scrim
    fireEvent.click(screen.getByTestId("recipient")); // inside: not a close
    expect(onClose).toHaveBeenCalledTimes(3);
    expect(confirm).not.toHaveBeenCalled();
  });

  it("asks before discarding an edit and keeps it when the analyst declines", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const onClose = vi.fn();
    render(<ApprovalDrawer detail={detail([draft()])} onClose={onClose} />);
    edit("Hi Ana, call us today.");
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(confirm).toHaveBeenCalledWith("Discard your edit to this draft?");
    expect(onClose).not.toHaveBeenCalled();
    expect(textarea().value).toBe("Hi Ana, call us today.");

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not ask once the edit was recorded", () => {
    decideState({ isSuccess: true });
    const confirm = vi.spyOn(window, "confirm");
    const onClose = vi.fn();
    render(<ApprovalDrawer detail={detail([draft()])} onClose={onClose} />);
    edit("Hi Ana, call us today.");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(confirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("returns focus to the opener, or to the case title when the opener is gone", () => {
    const title = document.createElement("h1");
    title.id = "case-title";
    title.tabIndex = -1;
    const opener = document.createElement("button");
    document.body.append(title, opener);

    opener.focus();
    const first = render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    first.unmount();
    expect(document.activeElement).toBe(opener);

    opener.focus();
    const second = render(<ApprovalDrawer detail={detail([draft()])} onClose={() => undefined} />);
    opener.remove();
    second.unmount();
    expect(document.activeElement).toBe(title);
    title.remove();
  });
});
