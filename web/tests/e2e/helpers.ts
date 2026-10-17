import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

export type Persona = "ana.analyst" | "sam.supervisor" | "adi.admin" | "tia.tenantb";
const LABELS: Record<Persona, string> = {
  "ana.analyst": "Ana (analyst)",
  "sam.supervisor": "Sam (supervisor)",
  "adi.admin": "Adi (admin)",
  "tia.tenantb": "Tia (analyst, tenant B)",
};

/** Sign in through the real flow: console → dev-oidc persona picker → callback. */
export async function signIn(page: Page, persona: Persona = "ana.analyst"): Promise<void> {
  await page.goto("/auth/login?returnTo=/queue");
  await page.getByRole("button", { name: LABELS[persona] }).click();
  await page.waitForURL("**/queue");
  await expect(page.getByTestId("whoami")).toContainText(persona);
}

/** WCAG 2.2 AA: no serious or critical axe violations on the current page. */
export async function expectAccessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(serious.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
}

export const problem = (status: number, detail: string) => ({
  status,
  contentType: "application/problem+json",
  body: JSON.stringify({
    type: "about:blank",
    title: status >= 500 ? "Internal Server Error" : "Error",
    status,
    detail,
    correlation_id: "e2e-correlation-0001",
  }),
});

type QueueItem = { account_id: string; opted_out: boolean; case_id: string | null };

/** A case paused at human approval, created through the BFF as the signed-in user. */
export async function caseAwaitingApproval(page: Page): Promise<string> {
  const headers = { "x-cc-csrf": "1", "content-type": "application/json" };
  const queue = await page.request.get("/api/queue?limit=40&status=no_case");
  expect(queue.ok()).toBeTruthy();
  const items = ((await queue.json()) as { items: QueueItem[] }).items.filter((i) => !i.opted_out);
  for (const item of items.slice(0, 15)) {
    const created = await page.request.post("/api/cases", {
      headers,
      data: { account_ids: [item.account_id] },
    });
    if (!created.ok()) continue;
    const caseId = ((await created.json()) as { cases: { id: string }[] }).cases[0]?.id;
    if (!caseId) continue;
    for (let i = 0; i < 120; i++) {
      const res = await page.request.get(`/api/cases/${caseId}`);
      const status = ((await res.json()) as { case: { status: string } }).case.status;
      if (status === "awaiting_approval") return caseId;
      if (
        ["closed", "failed", "requires_analyst", "escalated", "sent", "awaiting_reply"].includes(
          status,
        )
      )
        break;
      await page.waitForTimeout(250);
    }
  }
  throw new Error("no case reached awaiting_approval");
}
