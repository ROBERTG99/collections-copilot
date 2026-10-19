/** Reading and writing the session cookie in route handlers and server components. */
import "server-only";

import { cookies } from "next/headers";

import { webConfig } from "@/lib/config";
import { refreshSession } from "@/lib/oidc";
import {
  needsRefresh,
  openSession,
  REFRESH_GRACE_S,
  sealSession,
  SESSION_COOKIE,
  type Session,
} from "@/lib/session";

export async function currentSession(): Promise<Session | null> {
  const cfg = webConfig();
  const jar = await cookies();
  return openSession(jar.get(SESSION_COOKIE)?.value, cfg.sessionSecret);
}

export async function writeSession(session: Session): Promise<void> {
  const cfg = webConfig();
  const jar = await cookies();
  jar.set(SESSION_COOKIE, await sealSession(session, cfg.sessionSecret), {
    httpOnly: true,
    secure: cfg.secureCookies,
    sameSite: "lax",
    path: "/",
    maxAge: Math.max(60, session.expiresAt - Math.floor(Date.now() / 1000) + REFRESH_GRACE_S),
  });
}

export async function clearSession(): Promise<void> {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
}

/** The session with a usable access token, refreshing it when it is about to expire. */
export async function activeSession(): Promise<Session | null> {
  const session = await currentSession();
  if (!session) return null;
  if (!needsRefresh(session)) return session;
  const refreshed = await refreshSession(webConfig(), session);
  if (!refreshed) return null;
  await writeSession(refreshed);
  return refreshed;
}
