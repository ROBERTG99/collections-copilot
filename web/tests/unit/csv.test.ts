import { describe, expect, it } from "vitest";

import { csvCell } from "@/app/(console)/audit/audit-view";

describe("csvCell", () => {
  it("neutralises spreadsheet formulas from debtor text (M4 review)", () => {
    expect(csvCell('=HYPERLINK("http://x")')).toBe(`"'=HYPERLINK(""http://x"")"`);
    expect(csvCell("+1555")).toBe(`"'+1555"`);
    expect(csvCell("@SUM(A1)")).toBe(`"'@SUM(A1)"`);
    expect(csvCell("plain, text")).toBe(`"plain, text"`);
    expect(csvCell(null)).toBe(`""`);
  });
});
