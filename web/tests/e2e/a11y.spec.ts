import { expect, type Page, test } from "@playwright/test";

import { caseAwaitingApproval, expectAccessible, getJson, signIn } from "./helpers";
import { SCREENS } from "./screens";

async function noHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "page scrolls horizontally").toBeLessThanOrEqual(1);
}

/** WCAG 2.2 AA on every screen at 375, 768 and 1280 px, light and dark; no horizontal scroll.
 * One sign-in per persona per pass keeps the run inside the API's 120 requests/minute. */
const PASSES = [
  { width: 375, scheme: "light" },
  { width: 768, scheme: "light" },
  { width: 1280, scheme: "light" },
  { width: 375, scheme: "dark" },
  { width: 1280, scheme: "dark" },
] as const;

for (const { width, scheme } of PASSES) {
  test(`a11y: every screen at ${width} px, ${scheme}`, async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width, height: width < 768 ? 812 : 900 });
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
    await expectAccessible(page);
    let persona: string | null = null;
    for (const screen of SCREENS) {
      await test.step(screen.name, async () => {
        if (persona !== screen.persona) {
          await signIn(page, screen.persona);
          persona = screen.persona;
        }
        await screen.open(page);
        await expectAccessible(page);
        await noHorizontalScroll(page);
      });
    }
  });
}

test("a11y: Template chip, drawer in edit mode and error states", async ({ page }) => {
  await signIn(page);
  const caseId = await caseAwaitingApproval(page);
  // A deterministic fallback draft (no seeded case has one): the "Template" chip must pass too.
  const real = await getJson<Record<string, unknown>>(page, `/api/cases/${caseId}`);
  await page.route(`**/api/cases/${caseId}`, (route) => {
    const detail = structuredClone(real) as {
      case: { fallback: boolean };
      drafts: { author: string }[];
    };
    detail.case.fallback = true;
    for (const d of detail.drafts) d.author = "fallback";
    return route.fulfill({ json: detail });
  });
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto(`/cases/${caseId}`);
    await expect(page.getByText("Template").first()).toBeVisible();
    await page.getByRole("button", { name: /Review draft/ }).click();
    await page.getByRole("button", { name: "Edit" }).click();
    await page.getByLabel(/Message/).fill("Hello. Pay USD 999.00 today. Reply STOP to opt out.");
    await expect(page.getByTestId("postcheck-violations")).toBeVisible();
    await expect(page.getByTestId("diff")).toBeVisible();
    await expectAccessible(page);
    page.once("dialog", (d) => void d.accept()); // "Discard your edit?"
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: /Review SMS draft/ })).toBeHidden();
  }
});

test("keyboard: skip link, focusable navigation and the drawer keeps focus", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  const focused = await page.evaluate(() => document.activeElement?.closest("main") !== null);
  expect(focused).toBe(true);
  await expect(page.getByRole("link", { name: "Queue" })).toHaveAttribute("aria-current", "page");

  const caseId = await caseAwaitingApproval(page);
  await page.goto(`/cases/${caseId}`);
  const trigger = page.getByRole("button", { name: /Review draft/ });
  await trigger.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    // A modal dialog cycles through its controls and the browser's own UI (body), never the page.
    const inside = await page.evaluate(() => {
      const el = document.activeElement;
      return !el || el === document.body || !!el.closest("dialog");
    });
    expect(inside, `Tab ${i + 1} reached the page behind the drawer`).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});
