import { expect, test } from "@playwright/test";

import { expectAccessible, getJson, problem, signIn } from "./helpers";

type Ops = {
  pending_approvals: number;
  sends: number;
  replies: number;
  opt_outs: number;
  follow_ups_pending: number;
  cases_by_status: Record<string, number>;
};

test.describe("ops dashboard", () => {
  test("every card equals GET /api/v1/metrics/ops", async ({ page }) => {
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
          };
          return (
            ui.pending === String(api.pending_approvals) &&
            ui.sends === String(api.sends) &&
            ui.replies === String(api.replies) &&
            ui.optOuts === String(api.opt_outs) &&
            ui.followUps === String(api.follow_ups_pending)
          );
        },
        { timeout: 30_000, intervals: [500, 1000, 2000] },
      )
      .toBe(true);
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
