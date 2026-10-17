import { describe, expect, it } from "vitest";

import { formatMoney, humanize, percent } from "@/lib/format";

describe("format", () => {
  it("formats integer cents with the currency", () => {
    expect(formatMoney(123456, "USD")).toBe("$1,234.56");
    expect(formatMoney(5, "EUR")).toBe("€0.05");
  });
  it("formats confidence as a whole percentage", () => {
    expect(percent(0.925)).toBe("93%");
    expect(percent(null)).toBe("—");
  });
  it("humanizes codes", () => {
    expect(humanize("payment_plan")).toBe("payment plan");
  });
});
