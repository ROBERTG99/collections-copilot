import { expect, test } from "@playwright/test";

import { expectAccessible, signIn } from "./helpers";
import { SCREENS } from "./screens";

/** WCAG 2.2 AA on every screen at a phone and a desktop width; no horizontal scrolling. */
for (const width of [375, 1440]) {
  test.describe(`a11y at ${width} px`, () => {
    test.use({ viewport: { width, height: width === 375 ? 812 : 900 } });
    for (const screen of SCREENS) {
      test(screen.name, async ({ page }) => {
        await signIn(page, screen.persona);
        await screen.open(page);
        await expectAccessible(page);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, "page scrolls horizontally").toBeLessThanOrEqual(1);
      });
    }
  });
}

test("keyboard: skip link and focusable navigation", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  const focused = await page.evaluate(() => document.activeElement?.closest("main") !== null);
  expect(focused).toBe(true);
});
