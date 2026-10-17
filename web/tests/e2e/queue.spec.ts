import { expect, test } from "@playwright/test";

import { expectAccessible, problem, signIn } from "./helpers";

test.describe("queue", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("success: accounts sorted by score, filterable, accessible", async ({ page }) => {
    const table = page.getByRole("table", { name: /accounts by priority/i });
    await expect(table).toBeVisible();
    const scores = await table.locator("tbody tr td:nth-child(6)").allInnerTexts();
    expect(scores.length).toBeGreaterThan(10);
    const numbers = scores.map(Number);
    expect([...numbers].sort((a, b) => b - a)).toEqual(numbers);
    await page.getByLabel("Segment").selectOption("enterprise");
    await expect(table.locator("tbody tr").first()).toContainText("enterprise");
    const segments = await table.locator("tbody tr td:nth-child(3)").allInnerTexts();
    expect(new Set(segments)).toEqual(new Set(["enterprise"]));
    await expectAccessible(page);
  });

  test("empty: a search with no match explains the next step", async ({ page }) => {
    await page.getByLabel("Account or reference").fill("zz-no-such-account");
    await page.getByRole("button", { name: "Search" }).click();
    await expect(page.getByText("No accounts match these filters")).toBeVisible();
    await expectAccessible(page);
  });

  test("error: problem details show the correlation id and retry works", async ({ page }) => {
    let fail = true;
    await page.route("**/api/queue**", async (route) => {
      if (fail) return route.fulfill(problem(500, "database unavailable"));
      return route.continue();
    });
    await page.reload();
    const alert = page.getByRole("main").getByRole("alert");
    await expect(alert).toContainText("database unavailable");
    await expect(alert).toContainText("e2e-correlation-0001");
    await expectAccessible(page);
    fail = false;
    await alert.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByRole("table", { name: /accounts by priority/i })).toBeVisible();
  });

  test("loading: a skeleton is announced while the queue loads", async ({ page }) => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/api/queue**", async (route) => {
      await gate;
      await route.continue();
    });
    await page.reload();
    await expect(page.getByRole("status", { name: "Loading the queue" })).toBeVisible();
    release();
    await expect(page.getByRole("table", { name: /accounts by priority/i })).toBeVisible();
  });
});

test.describe("account detail", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("success: aging, invoices, notes and the live pre-check", async ({ page }) => {
    const first = page
      .getByRole("table", { name: /accounts by priority/i })
      .getByRole("link")
      .first();
    const name = await first.innerText();
    await first.click();
    await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Aging/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Compliance pre-check/ })).toBeVisible();
    await expect(page.getByTestId("priority-score")).toHaveText(/\d+/);
    await expectAccessible(page);
  });

  test("error and loading states", async ({ page }) => {
    await page.route("**/api/accounts/**", (route) =>
      route.fulfill(problem(404, "account not found")),
    );
    await page.goto("/accounts/00000000-0000-0000-0000-000000000000");
    await expect(page.getByRole("main").getByRole("alert")).toContainText("account not found");
    await expect(page.getByRole("main").getByRole("alert")).toContainText("e2e-correlation-0001");
    await page.unroute("**/api/accounts/**");
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/api/accounts/**", async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto("/queue");
    const link = page
      .getByRole("table", { name: /accounts by priority/i })
      .getByRole("link")
      .first();
    await link.click();
    await expect(page.getByRole("status", { name: "Loading the account" })).toBeVisible();
    release();
    await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();
  });

  test("empty: an account without history offers to start a case", async ({ page }) => {
    // The real account with its history removed: the empty sections must say what to do next.
    await page.route("**/api/accounts/**", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      const empty = { ...body, cases: [], notes: [], messages: [], promises: [], opted_out: false };
      await route.fulfill({ response, json: empty });
    });
    await page.getByLabel("Case status").selectOption("no_case");
    await page
      .getByRole("table", { name: /accounts by priority/i })
      .getByRole("link")
      .first()
      .click();
    await expect(page.getByText("No cases yet")).toBeVisible();
    await expect(page.getByText("No notes")).toBeVisible();
    await expect(page.getByRole("button", { name: "Start case" })).toBeEnabled();
    await expectAccessible(page);
  });
});
