/**
 * BFF proxy helpers. The browser may reach only `/api/v1/*` minus `/api/v1/webhooks/*`
 * (provider callbacks are signed and go to the API directly). Next decodes `%2F` and `%2E`
 * inside a segment, so every segment is checked after decoding and the final URL again.
 */

const FORBIDDEN_SEGMENT = /[/\\]|^\.{1,2}$|^$/;

export function apiTarget(base: string, path: string[], search: string): string | null {
  if (path.length === 0 || path.some((p) => FORBIDDEN_SEGMENT.test(p))) return null;
  if ((path[0] ?? "").toLowerCase() === "webhooks") return null;
  const target = `${base}/api/v1/${path.map(encodeURIComponent).join("/")}${search}`;
  const pathname = new URL(target).pathname.toLowerCase();
  if (!pathname.startsWith("/api/v1/") || pathname.startsWith("/api/v1/webhooks")) return null;
  return target;
}

/** The request body, or null when it is larger than `max` bytes (read with a running cap). */
export async function readCapped(request: Request, max: number): Promise<ArrayBuffer | null> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > max) return null;
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > max) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out.buffer;
}
