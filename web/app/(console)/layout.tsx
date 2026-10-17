import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { UserProvider } from "@/components/user-context";
import { QueryProvider } from "@/lib/api/query";
import { currentSession } from "@/lib/server-session";
import { hasRole } from "@/lib/session";

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
  if (!session) redirect("/auth/login");
  return (
    <QueryProvider>
      <UserProvider user={session.user}>
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <header className="border-b border-rule bg-surface">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-4 py-2">
            <span className="font-semibold">collections-copilot</span>
            <nav aria-label="Main" className="flex flex-wrap gap-3">
              {NAV.filter((n) => !n.roles || hasRole(session, ...n.roles)).map((n) => (
                <Link key={n.href} href={n.href} className="rounded px-2 py-1 hover:bg-surface-2">
                  {n.label}
                </Link>
              ))}
            </nav>
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
