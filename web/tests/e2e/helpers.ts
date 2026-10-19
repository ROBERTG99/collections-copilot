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

type QueueItem = {
  account_id: string;
  opted_out: boolean;
  contact_flag: string | null;
  case_id: string | null;
  timezone: string;
  max_days_past_due: number;
};

/** Accounts this run already turned into a case (most end closed: escalate, pause, no action). */
const tried = new Set<string>();

/** GET through the BFF as the signed-in user, polling past 429s (the API allows 120/min). */
export async function getJson<T>(page: Page, url: string): Promise<T> {
  let body: T | undefined;
  await expect
    .poll(
      async () => {
        const res = await page.request.get(url);
        if (res.status() === 429) return 429;
        expect(res.ok(), `${url} → ${res.status()}`).toBeTruthy();
        body = (await res.json()) as T;
        return res.status();
      },
      { timeout: 90_000, intervals: [1_000, 2_000, 5_000, 10_000] },
    )
    .not.toBe(429);
  return body as T;
}

/** Inside the 08:00–21:00 contact window in the account's own time zone right now. */
export function insideWindow(timeZone: string, now = new Date()): boolean {
  const hour = Number(
    new Intl.DateTimeFormat("en-US", { timeZone, hour: "numeric", hourCycle: "h23" }).format(now),
  );
  return hour >= 8 && hour < 21;
}

const DONE = ["closed", "failed", "requires_analyst", "escalated", "sent", "awaiting_reply"];

/** A case paused at human approval, created through the BFF as the signed-in user. Uses only
 * accounts inside their contact window now (docs/KNOWN_LIMITATIONS.md: around 04:00–06:00 UTC
 * no seeded account is). */
export async function caseAwaitingApproval(page: Page, phoneEndsWith?: string): Promise<string> {
  const headers = { "x-cc-csrf": "1", "content-type": "application/json" };
  const queue = await getJson<{ items: QueueItem[] }>(page, "/api/queue?limit=100&status=no_case");
  const more = await getJson<{ items: QueueItem[] }>(
    page,
    "/api/queue?limit=100&status=no_case&min_score=0&segment=smb",
  );
  queue.items.push(
    ...more.items.filter((i) => !queue.items.some((q) => q.account_id === i.account_id)),
  );
  // The least overdue accounts are the likeliest to get a reminder (not an escalation).
  const candidates = queue.items
    .filter(
      (i) =>
        !i.opted_out && !i.contact_flag && !tried.has(i.account_id) && insideWindow(i.timezone),
    )
    .sort((a, b) => a.max_days_past_due - b.max_days_past_due);
  if (candidates.length === 0) {
    throw new Error("no seeded account is inside its 08:00–21:00 contact window right now");
  }
  for (const item of candidates.slice(0, 25)) {
    if (phoneEndsWith) {
      const account = await getJson<{ phone_e164: string }>(
        page,
        `/api/accounts/${item.account_id}`,
      );
      if (!phoneEndsWith.includes(account.phone_e164.slice(-1))) continue;
    }
    tried.add(item.account_id);
    const created = await page.request.post("/api/cases", {
      headers,
      data: { account_ids: [item.account_id] },
    });
    if (!created.ok()) continue;
    const caseId = ((await created.json()) as { cases: { id: string }[] }).cases[0]?.id;
    if (!caseId) continue;
    let status = "queued";
    await expect
      .poll(
        async () => {
          status = (await getJson<{ case: { status: string } }>(page, `/api/cases/${caseId}`)).case
            .status;
          return status === "awaiting_approval" || DONE.includes(status);
        },
        { timeout: 60_000, intervals: [500, 1_000, 2_000] },
      )
      .toBe(true);
    if (status === "awaiting_approval") return caseId;
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
