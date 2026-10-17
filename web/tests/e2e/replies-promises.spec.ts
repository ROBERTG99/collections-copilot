import { expect, test } from "@playwright/test";

import { approveCase, caseAwaitingApproval, expectAccessible, problem, signIn } from "./helpers";

test.describe("replies", () => {
  test("a simulated debtor reply is classified and can be reclassified", async ({ page }) => {
    await signIn(page);
    // Debtor-sim personas: numbers ending 0-6 always reply (promise, dispute or paid).
    const caseId = await caseAwaitingApproval(page, "0123456");
    await approveCase(page, caseId);
    await page.goto("/replies");
    const replies = page.getByTestId("replies");
    const mine = replies.locator("li").filter({ has: page.locator(`a[href="/cases/${caseId}"]`) });
    // The debtor simulator answers within seconds; reload until this case's reply is listed.
    await expect
      .poll(
        async () => {
          await page.reload();
          await expect(replies.or(page.getByText("No replies yet"))).toBeVisible();
          return mine.count();
        },
        { timeout: 45_000, intervals: [1000, 2000] },
      )
      .toBe(1);
    await expect(mine.locator("span").first()).not.toHaveText("unclassified");
    await expectAccessible(page);
    await mine.getByRole("button", { name: "Reclassify" }).click();
    await mine.getByLabel("New label").selectOption("other");
    await mine.getByRole("button", { name: "Save label" }).click();
    await expect(mine.locator("span").first()).toHaveText("other");
  });

  test("empty and error states", async ({ page }) => {
    await signIn(page);
    await page.route("**/api/replies**", (route) => route.fulfill({ json: [] }));
    await page.goto("/replies");
    await expect(page.getByText("No replies yet")).toBeVisible();
    await page.unroute("**/api/replies**");
    await page.route("**/api/replies**", (route) => route.fulfill(problem(503, "queue down")));
    await page.reload();
    await expect(page.getByRole("main").getByRole("alert")).toContainText("e2e-correlation-0001");
  });
});

const HELD = {
  id: "11111111-1111-1111-1111-111111111111",
  account_id: "22222222-2222-2222-2222-222222222222",
  case_id: null,
  amount_cents: 800000,
  currency: "USD",
  promised_date: "2026-11-02",
  invoice_numbers: ["INV-1"],
  status: "approval_required",
  mcp_approval_id: "apr-123",
  ledger_ref: null,
  failure_reason: null,
  created_at: "2026-10-17T10:00:00Z",
};

test.describe("promises", () => {
  test("supervisor approves a held promise", async ({ page }) => {
    await signIn(page, "sam.supervisor");
    await page.route("**/api/promises?**", (route) => route.fulfill({ json: [HELD] }));
    let approved = false;
    await page.route(`**/api/promises/${HELD.id}:approve`, (route) => {
      approved = true;
      return route.fulfill({
        json: { promise: { ...HELD, status: "approval_required" }, gateway_status: "approved" },
      });
    });
    await page.goto("/promises");
    const table = page.getByTestId("promises");
    await expect(table).toContainText("$8,000.00");
    await expect(table).toContainText("apr-123");
    await expectAccessible(page);
    await table.getByRole("button", { name: "Reject" }).isDisabled();
    await table.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Decision sent" })).toBeVisible();
    expect(approved).toBe(true);
  });

  test("analysts see the hold but cannot decide; states render", async ({ page }) => {
    await signIn(page);
    await page.route("**/api/promises?**", (route) => route.fulfill({ json: [HELD] }));
    await page.goto("/promises");
    await expect(page.getByText("Waiting for a supervisor")).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
    await page.unroute("**/api/promises?**");
    await page.route("**/api/promises?**", (route) => route.fulfill({ json: [] }));
    await page.reload();
    await expect(page.getByText("No promises yet")).toBeVisible();
  });
});
