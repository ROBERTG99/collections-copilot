import { describe, expect, it } from "vitest";

import { decisionKey } from "@/lib/api/hooks";

describe("decisionKey", () => {
  it("changes with the edited body, so a refused edit never blocks a corrected one", () => {
    const refused = decisionKey("c1", {
      decision: "edit",
      draft_version: 2,
      body: "Pay USD 999.00",
    });
    const fixed = decisionKey("c1", { decision: "edit", draft_version: 2, body: "Pay USD 99.00" });
    expect(refused).not.toBe(fixed);
    expect(decisionKey("c1", { decision: "edit", draft_version: 2, body: "Pay USD 99.00" })).toBe(
      fixed,
    );
  });
  it("separates decisions, versions and reasons", () => {
    const approve = decisionKey("c1", { decision: "approve", draft_version: 2 });
    expect(approve).not.toBe(decisionKey("c1", { decision: "approve", draft_version: 3 }));
    expect(decisionKey("c1", { decision: "reject", draft_version: 2, reason: "a" })).not.toBe(
      decisionKey("c1", { decision: "reject", draft_version: 2, reason: "b" }),
    );
    expect(approve).toMatch(/^[\w:-]{1,200}$/); // a valid Idempotency-Key for the API
  });
});
