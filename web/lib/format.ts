/** Formatting at the edge only: money stays integer cents everywhere else. */
export function formatMoney(cents: number, currency: string, locale = "en-US"): string {
  return new Intl.NumberFormat(locale, { style: "currency", currency }).format(cents / 100);
}

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** A calendar date. Date-only values ("2026-11-02") are shown as written, in any browser zone. */
export function formatDate(value: string | null | undefined, locale = "en-US"): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    ...(DATE_ONLY.test(value) ? { timeZone: "UTC" } : {}),
  }).format(date);
}

/** A point in time; with `timeZone` it is shown in that zone and names it ("8:00 AM EDT"). */
export function formatDateTime(
  value: string | null | undefined,
  options: { timeZone?: string; locale?: string } = {},
): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const { timeZone, locale = "en-US" } = options;
  try {
    return (
      new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
        ...(timeZone ? { timeZone } : {}),
      }).format(date) + (timeZone ? ` ${zoneName(date, timeZone, locale)}` : "")
    );
  } catch {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(
      date,
    );
  }
}

function zoneName(date: Date, timeZone: string, locale: string): string {
  const part = new Intl.DateTimeFormat(locale, { timeZone, timeZoneName: "short" })
    .formatToParts(date)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? timeZone;
}

export function percent(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

export function humanize(code: string | null | undefined): string {
  return code ? code.replaceAll("_", " ") : "—";
}
