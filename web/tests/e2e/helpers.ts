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
