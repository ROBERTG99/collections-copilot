import { expect, test } from "@playwright/test";

import { decisionKey } from "@/lib/api/hooks";

import { caseAwaitingApproval, expectAccessible, signIn } from "./helpers";

test.describe("approval drawer", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("failing post-check blocks approve", async ({ page }) => {
    const caseId = await caseAwaitingApproval(page);
    await page.goto(`/cases/${caseId}`);
    await page.getByRole("button", { name: /Review draft/ }).click();
    const drawer = page.getByRole("dialog", { name: /Review SMS draft/ });
    await expect(drawer.getByTestId("postcheck-ok")).toBeVisible();
    await expect(drawer.getByTestId("recipient")).toHaveText(/^\+1555|^\+34555/);
    await expectAccessible(page);
    await drawer.getByRole("button", { name: "Edit" }).click();
    const box = drawer.getByLabel(/Message/);
    await box.fill(`${await box.inputValue()} Pay USD 999.00 today.`);
    await expect(drawer.getByTestId("postcheck-violations")).toContainText("amount not in ledger");
    await expect(drawer.getByRole("button", { name: "Save edit and approve" })).toBeDisabled();
    await drawer.getByRole("button", { name: "Reject" }).click();
    await expect(drawer.getByRole("button", { name: "Reject draft" })).toBeDisabled();
    page.once("dialog", (d) => void d.accept()); // "Discard your edit to this draft?"
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
  });

  test("a refused edit never locks the corrected one (M4 review B1)", async ({ page }) => {
    const caseId = await caseAwaitingApproval(page);
    const detail = (await (await page.request.get(`/api/cases/${caseId}`)).json()) as {
      drafts: { version: number; body: string }[];
    };
    const draft = detail.drafts.at(-1)!;
    // The edit the old drawer let through before its check caught up: refused with 422.
    const bad = {
      decision: "edit" as const,
      draft_version: draft.version,
      body: `${draft.body} Pay USD 999.00 today.`,
    };
    const refused = await page.request.post(`/api/cases/${caseId}/approval`, {
      headers: {
        "x-cc-csrf": "1",
        "content-type": "application/json",
        "idempotency-key": decisionKey(caseId, bad),
      },
      data: bad,
    });
    expect(refused.status()).toBe(422);

    await page.goto(`/cases/${caseId}`);
    await page.getByRole("button", { name: /Review draft/ }).click();
    const drawer = page.getByRole("dialog", { name: /Review SMS draft/ });
    await drawer.getByRole("button", { name: "Edit" }).click();
    const box = drawer.getByLabel(/Message/);
    await box.fill(bad.body);
    // Text that has not been checked yet can never be approved, not even for 300 ms.
    await expect(drawer.getByRole("button", { name: "Save edit and approve" })).toBeDisabled();
    await expect(drawer.getByTestId("postcheck-violations")).toContainText("amount not in ledger");
    await box.fill(draft.body.replace(/^Hi /, "Hello ").replace(/^Hola /, "Buenos días "));
    await expect(drawer.getByTestId("postcheck-ok")).toBeVisible();
    await drawer.getByRole("button", { name: "Save edit and approve" }).click();
    await expect(drawer.getByRole("status")).toContainText("Decision recorded");
  });

  test("edit shows the diff, re-checks and approves the edited text", async ({ page }) => {
    const caseId = await caseAwaitingApproval(page);
    await page.goto(`/cases/${caseId}`);
    await expect(page.getByTestId("progress")).toContainText("human approval");
    await page.getByRole("button", { name: /Review draft/ }).click();
    const drawer = page.getByRole("dialog", { name: /Review SMS draft/ });
    await drawer.getByRole("button", { name: "Edit" }).click();
    const box = drawer.getByLabel(/Message/);
    const original = await box.inputValue();
    const edited = original.replace(/^Hi /, "Hello ").replace(/^Hola /, "Buenos días ");
    expect(edited).not.toEqual(original);
    await box.fill(edited);
    const diff = drawer.getByTestId("diff");
    await expect(diff.locator("ins").first()).toBeVisible();
    await expect(diff.locator("del").first()).toBeVisible();
    await expect(drawer.getByTestId("postcheck-ok")).toBeVisible();
    await drawer.getByRole("button", { name: "Save edit and approve" }).click();
    await expect(drawer.getByRole("status")).toContainText("Decision recorded");
    await page.keyboard.press("Escape");
    // The worker resumes, re-checks and sends: the live stream moves the case on.
    await expect(page.getByTestId("case-status")).not.toContainText("awaiting approval", {
      timeout: 30_000,
    });
    await expect(page.getByText(/edit by ana\.analyst/)).toBeVisible();
  });
});
