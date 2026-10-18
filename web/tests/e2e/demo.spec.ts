import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { expectAccessible, signIn } from "./helpers";

/**
 * T-49: the §3.4 main journey in the console, recorded to docs/images/demo.webm. The account comes
 * from `scripts/demo.py --prepare main` (a USD 1,200.00 balance, a note about instalments, a
 * time zone where it is midday, a debtor-sim number that promises the balance on Friday).
 */
const ROOT = path.resolve(import.meta.dirname, "../../..");
const VIDEO = path.join(ROOT, "docs/images/demo.webm");
const FOOTER = "Reply STOP to opt out.";

type Prepared = { account_id: string; external_ref: string; name: string };

function prepare(): Prepared {
  const out = execFileSync(
    "uv",
    ["run", "--frozen", "python", "scripts/demo.py", "--prepare", "main"],
    {
      cwd: ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    },
  );
  return JSON.parse(out.trim().split("\n").at(-1) ?? "{}") as Prepared;
}

/** Reload the page until `text` shows (the debtor-sim reply and the ledger write are async). */
async function reloadUntil(page: Page, text: RegExp, timeout = 90_000): Promise<void> {
  await expect
    .poll(
      async () => {
        await page.reload();
        return (await page.locator("main").innerText()).match(text) !== null;
      },
      { timeout, intervals: [2_000, 3_000, 5_000] },
    )
    .toBe(true);
}

// slowMo paces the recording for viewers; every assertion still waits on state, never on time.
test.use({
  video: { mode: "on", size: { width: 1280, height: 800 } },
  viewport: { width: 1280, height: 800 },
  launchOptions: { slowMo: 1000 },
});
test.setTimeout(240_000);

test("main journey: queue → NBA citing a note → edit and approve → promise written", async ({
  page,
}) => {
  const account = prepare();
  expect(account.external_ref).toMatch(/^DEMO\d{4}-01$/);

  await signIn(page, "ana.analyst");
  await page.getByLabel("Account or reference").fill(account.external_ref);
  await page.getByRole("button", { name: "Search" }).click();
  const table = page.getByRole("table", { name: /accounts by priority/i });
  await table.getByRole("link", { name: account.name }).click();

  await expect(page.getByRole("heading", { name: account.name })).toBeVisible();
  await expect(page.getByText(/pay in installments/)).toBeVisible();
  await expectAccessible(page);
  await page.getByRole("button", { name: "Start case" }).click();
  await page.getByRole("link", { name: /Open case/ }).click();

  await expect(page.getByTestId("progress")).toContainText("human approval", { timeout: 60_000 });
  await expect(page.getByText(/pay in installments/)).toBeVisible(); // the cited note
  await page.getByRole("button", { name: /Review draft/ }).click();
  const drawer = page.getByRole("dialog", { name: /Review SMS draft/ });
  await expect(drawer.getByTestId("recipient")).toHaveText(/^\+1555/);
  await expect(drawer.getByTestId("postcheck-ok")).toBeVisible();

  await drawer.getByRole("button", { name: "Edit" }).click();
  const box = drawer.getByLabel(/Message/);
  const original = await box.inputValue();
  expect(original.endsWith(FOOTER)).toBeTruthy();
  const head = original.slice(0, -FOOTER.length).trimEnd();
  await box.fill(`${head} Thank you for working with us. ${FOOTER}`);
  await expect(drawer.getByTestId("diff").locator("ins").first()).toBeVisible();
  await expect(drawer.getByTestId("postcheck-ok")).toBeVisible();
  await drawer.getByRole("button", { name: "Save edit and approve" }).click();
  await expect(drawer.getByRole("status")).toContainText("Decision recorded");
  await page.keyboard.press("Escape");

  // The live stream: the worker sends and waits for the reply.
  await expect(page.getByTestId("progress")).toContainText("send sms", { timeout: 60_000 });
  await expect(page.getByTestId("progress")).toContainText("await reply", { timeout: 60_000 });

  await page.goto(`/accounts/${account.account_id}`);
  await reloadUntil(page, /I can pay 1,200\.00 on Friday/);
  await expect(page.getByText(/promise to pay/i).first()).toBeVisible();

  await page.goto("/promises");
  await reloadUntil(page, new RegExp(`${account.name}[\\s\\S]*Written`));
  await expectAccessible(page);

  const video = page.video();
  await page.close();
  await video?.saveAs(VIDEO);
});
