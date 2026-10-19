import { describe, expect, it } from "vitest";

import { formatDate, formatDateTime, formatMoney, humanize, percent } from "@/lib/format";

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
  it("shows date-only values as written, whatever the browser zone (M4 review)", () => {
    expect(new Date("2026-11-02").getTimezoneOffset()).toBeGreaterThan(0); // really west of UTC
    expect(formatDate("2026-11-02")).toBe("Nov 2, 2026");
    expect(formatDate(null)).toBe("—");
  });
  it("shows a time in the debtor's zone with the zone named", () => {
    const at = "2026-03-10T13:00:00+00:00";
    expect(formatDateTime(at, { timeZone: "America/New_York" })).toBe("Mar 10, 2026, 9:00 AM EDT");
    expect(formatDateTime(at, { timeZone: "Europe/Madrid" })).toBe("Mar 10, 2026, 2:00 PM GMT+1");
  });
});
