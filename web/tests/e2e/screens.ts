import { expect, type Page } from "@playwright/test";

import { caseAwaitingApproval, getJson, type Persona } from "./helpers";

export type Screen = {
  name: string;
  persona: Persona;
  open: (page: Page) => Promise<void>;
};

async function firstAccount(page: Page): Promise<string> {
  const queue = await getJson<{ items: { account_id: string }[] }>(page, "/api/queue?limit=1");
  const id = queue.items[0]?.account_id;
  if (!id) throw new Error("the queue is empty; run make seed");
  return id;
}

let pausedCase: string | null = null;
async function caseId(page: Page): Promise<string> {
  if (!pausedCase) pausedCase = await caseAwaitingApproval(page);
  return pausedCase;
}

/** Every console screen, in the order of the user journey (BUILD.md §13). */
export const SCREENS: Screen[] = [
  {
    name: "queue",
    persona: "ana.analyst",
    open: async (page) => {
      await page.goto("/queue");
      await expect(page.getByRole("heading", { name: "Prioritized queue" })).toBeVisible();
      await expect(page.locator(":is(tbody tr, main ul li):visible").first()).toBeVisible();
    },
  },
  {
    name: "account",
    persona: "ana.analyst",
    open: async (page) => {
      await page.goto(`/accounts/${await firstAccount(page)}`);
      await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();
    },
  },
  {
    name: "case",
    persona: "ana.analyst",
    open: async (page) => {
      await page.goto(`/cases/${await caseId(page)}`);
      await expect(page.getByRole("heading", { name: "Drafts" })).toBeVisible();
    },
  },
  {
    name: "approval-drawer",
    persona: "ana.analyst",
    open: async (page) => {
      await page.goto(`/cases/${await caseId(page)}`);
      await page.getByRole("button", { name: /Review draft/ }).click();
      await expect(
        page.getByTestId("postcheck-ok").or(page.getByTestId("postcheck-violations")),
      ).toBeVisible();
    },
  },
  {
    name: "replies",
    persona: "ana.analyst",
    open: async (page) => {
      await page.goto("/replies");
      await expect(page.getByRole("heading", { name: "Replies" })).toBeVisible();
      await expect(page.getByTestId("replies").or(page.getByText("No replies yet"))).toBeVisible();
    },
  },
  {
    name: "promises",
    persona: "sam.supervisor",
    open: async (page) => {
      await page.goto("/promises");
      await expect(
        page.getByTestId("promises").or(page.getByText("No promises yet")),
      ).toBeVisible();
    },
  },
  {
    name: "audit",
    persona: "sam.supervisor",
    open: async (page) => {
      await page.goto("/audit");
      await expect(page.getByTestId("chain")).toBeVisible();
    },
  },
  {
    name: "ops",
    persona: "sam.supervisor",
    open: async (page) => {
      await page.goto("/ops");
      await expect(page.getByTestId("pending-approvals")).toBeVisible();
    },
  },
  {
    name: "settings",
    persona: "sam.supervisor",
    open: async (page) => {
      await page.goto("/settings");
      await expect(page.getByRole("button", { name: "Save" })).toBeVisible();
      await expect(page.getByTestId("opt-outs").or(page.getByText("No opt-outs"))).toBeVisible();
    },
  },
];
