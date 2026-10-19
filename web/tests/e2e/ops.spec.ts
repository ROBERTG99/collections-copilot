import { expect, test } from "@playwright/test";

import { expectAccessible, getJson, problem, signIn } from "./helpers";

type Ops = {
  pending_approvals: number;
  sends: number;
  replies: number;
  opt_outs: number;
  follow_ups_pending: number;
  contact_window_rate: number | null;
  cost_per_account_usd: number | null;
  cases_by_status: Record<string, number>;
  node_p95_ms: Record<string, number>;
  dlq_depth: Record<string, number | null>;
};

const usd4 = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
});
const human = (s: string) => s.replaceAll("_", " ");

/** What each FR-14 card and table must show for an API snapshot (counts by status, window
 * rate, pending approvals, cost per account, node p95, DLQ depth, plus the activity counts). */
function expected(api: Ops): Record<string, string> {
  return {
    pending: String(api.pending_approvals),
    sends: String(api.sends),
    replies: String(api.replies),
    optOuts: String(api.opt_outs),
    followUps: String(api.follow_ups_pending),
    window: api.contact_window_rate == null ? "—" : `${Math.round(api.contact_window_rate * 100)}%`,
    cost: api.cost_per_account_usd == null ? "—" : usd4.format(api.cost_per_account_usd),
    dlq: Object.entries(api.dlq_depth)
      .map(([q, d]) => `${q}: ${d == null ? "unavailable" : d}`)
      .join("\n"),
    byStatus: Object.entries(api.cases_by_status)
      .map(([s, n]) => `${human(s)}\t${n}`)
      .join("\n"),
    p95: Object.entries(api.node_p95_ms)
      .map(([n, ms]) => `${human(n)}\t${ms} ms`)
      .join("\n"),
  };
}

test.describe("ops dashboard", () => {
  test("every FR-14 card and table equals GET /api/v1/metrics/ops", async ({ page }) => {
    await signIn(page, "sam.supervisor");
    await page.goto("/ops");
    await expect(page.getByTestId("pending-approvals")).toBeVisible();
    // The stack is live (worker, debtor-sim), so compare a snapshot taken right after a reload.
    await expect
      .poll(
        async () => {
          await page.reload();
          await expect(page.getByTestId("pending-approvals")).toBeVisible();
          const api = await getJson<Ops>(page, "/api/metrics/ops?window_days=30");
          const ui = {
            pending: await page.getByTestId("pending-approvals").innerText(),
            sends: await page.getByTestId("sends").innerText(),
            replies: await page.getByTestId("replies").innerText(),
            optOuts: await page.getByTestId("opt-outs").innerText(),
            followUps: await page.getByTestId("follow-ups").innerText(),
            window: await page.getByTestId("window-rate").innerText(),
            cost: await page.getByTestId("cost-per-account").innerText(),
            dlq: (await page.getByTestId("dlq").innerText()).trim(),
            byStatus: (await page.getByTestId("cases-by-status").innerText()).trim(),
            p95: (await page.getByTestId("node-p95").innerText()).trim(),
          };
          const want = expected(api);
          const diff = Object.keys(want).filter(
            (k) => ui[k as keyof typeof ui] !== want[k as keyof typeof want],
          );
          return diff.length === 0 ? "equal" : JSON.stringify({ diff, ui, want });
        },
        { timeout: 30_000, intervals: [500, 1000, 2000] },
      )
      .toBe("equal");
    await expectAccessible(page);
  });

  test("analysts do not see Ops; errors render", async ({ page }) => {
    await signIn(page);
    await expect(page.getByRole("link", { name: "Ops" })).toHaveCount(0);
    await page.goto("/ops");
    await expect(page.getByRole("main").getByRole("alert")).toContainText("403");
    await page.route("**/api/metrics/ops**", (route) =>
      route.fulfill(problem(500, "metrics unavailable")),
    );
    await page.reload();
    await expect(page.getByRole("main").getByRole("alert")).toContainText("metrics unavailable");
  });
});
