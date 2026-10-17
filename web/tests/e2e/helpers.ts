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

/** GET through the BFF as the signed-in user, waiting out 429s (the API allows 120/min). */
export async function getJson<T>(page: Page, url: string): Promise<T> {
  for (let attempt = 0; attempt < 10; attempt++) {
    const res = await page.request.get(url);
    if (res.status() === 429) {
      await page.waitForTimeout(1000 * Number(res.headers()["retry-after"] ?? "5"));
      continue;
    }
    expect(res.ok(), `${url} → ${res.status()}`).toBeTruthy();
    return (await res.json()) as T;
  }
  throw new Error(`${url} stayed rate limited`);
}

/** A case paused at human approval, created through the BFF as the signed-in user. */
export async function caseAwaitingApproval(page: Page, phoneEndsWith?: string): Promise<string> {
  const headers = { "x-cc-csrf": "1", "content-type": "application/json" };
  const queue = await getJson<{ items: QueueItem[] }>(page, "/api/queue?limit=100&status=no_case");
  const items = queue.items.filter((i) => !i.opted_out);
  for (const item of items.slice(0, 40)) {
    const account = await getJson<{
      phone_e164: string;
      precheck: { ok: boolean; deferred: boolean };
    }>(page, `/api/accounts/${item.account_id}`);
    // Only accounts that can be texted right now (inside their 08:00–21:00 window).
    if (!account.precheck.ok || account.precheck.deferred) continue;
    if (phoneEndsWith && !phoneEndsWith.includes(account.phone_e164.slice(-1))) continue;
    const created = await page.request.post("/api/cases", {
      headers,
      data: { account_ids: [item.account_id] },
    });
    if (!created.ok()) continue;
    const caseId = ((await created.json()) as { cases: { id: string }[] }).cases[0]?.id;
    if (!caseId) continue;
    for (let i = 0; i < 60; i++) {
      const detail = await getJson<{ case: { status: string } }>(page, `/api/cases/${caseId}`);
      const status = detail.case.status;
      if (status === "awaiting_approval") return caseId;
      if (
        ["closed", "failed", "requires_analyst", "escalated", "sent", "awaiting_reply"].includes(
          status,
        )
      )
        break;
      await page.waitForTimeout(1000);
    }
  }
  throw new Error("no case reached awaiting_approval");
}

/** Approve the latest draft of a case through the BFF, as the signed-in user. */
export async function approveCase(page: Page, caseId: string): Promise<void> {
  const detail = await getJson<{ drafts: { version: number }[] }>(page, `/api/cases/${caseId}`);
  const version = detail.drafts.at(-1)?.version ?? 1;
  const resp = await page.request.post(`/api/cases/${caseId}/approval`, {
    headers: { "x-cc-csrf": "1", "content-type": "application/json" },
    data: { decision: "approve", draft_version: version },
  });
  expect(resp.ok()).toBeTruthy();
}
