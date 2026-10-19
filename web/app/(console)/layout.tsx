import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { NavLinks } from "@/components/nav-links";
import { UserProvider } from "@/components/user-context";
import { QueryProvider } from "@/lib/api/query";
import { currentSession } from "@/lib/server-session";
import { hasRole, isExpired } from "@/lib/session";

const NAV = [
  { href: "/queue", label: "Queue" },
  { href: "/replies", label: "Replies" },
  { href: "/promises", label: "Promises" },
  { href: "/audit", label: "Audit", roles: ["supervisor", "admin"] },
  { href: "/ops", label: "Ops", roles: ["supervisor", "admin"] },
  { href: "/settings", label: "Settings" },
];

export default async function ConsoleLayout({ children }: { children: ReactNode }) {
  const session = await currentSession();
  const path = (await headers()).get("x-cc-path") ?? "/queue";
  const returnTo = encodeURIComponent(
    path.startsWith("/") && !path.startsWith("//") ? path : "/queue",
  );
  if (!session) redirect(`/auth/login?returnTo=${returnTo}`);
  // An expired access token is refreshed by a route handler (server components cannot set
  // cookies); if that fails the analyst signs in again instead of seeing every panel fail.
  if (isExpired(session)) {
    redirect(`/auth/refresh?returnTo=${returnTo}`);
  }
  return (
    <QueryProvider>
      <UserProvider user={session.user}>
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <header className="border-b border-rule bg-surface">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-4 py-2">
            <span className="font-semibold">collections-copilot</span>
            <NavLinks
              links={NAV.filter((n) => !n.roles || hasRole(session, ...n.roles)).map(
                ({ href, label }) => ({ href, label }),
              )}
            />
            <div className="ml-auto flex items-center gap-3 text-sm">
              <span data-testid="whoami">
                {session.user.username} · {session.user.roles.join(", ")}
              </span>
              <form action="/auth/logout" method="post">
                <button type="submit" className="rounded border border-rule px-2 py-1">
                  Sign out
                </button>
              </form>
            </div>
          </div>
        </header>
        <main id="main" className="mx-auto max-w-7xl px-4 py-4">
          {children}
        </main>
      </UserProvider>
    </QueryProvider>
  );
}
