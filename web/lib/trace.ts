/**
 * Trace headers for BFF → API calls (§14). The BFF starts each trace with a W3C `traceparent`
 * (the API's server span continues it, so the whole request shares one trace id) and sends a
 * correlation id that problem details echo back.
 */

const SAFE_ID = /^[A-Za-z0-9._:-]{1,128}$/;

function hex(bytes: number): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function traceparent(): string {
  let traceId = hex(16);
  while (/^0+$/.test(traceId)) traceId = hex(16);
  return `00-${traceId}-${hex(8)}-01`;
}

export function correlationId(fromClient: string | null): string {
  return fromClient && SAFE_ID.test(fromClient) ? fromClient : crypto.randomUUID();
}
