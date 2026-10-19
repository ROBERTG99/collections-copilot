"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { SessionUser } from "@/lib/session";

const UserContext = createContext<SessionUser | null>(null);

/** The signed-in user's name and roles (no tokens) for role-aware UI; the API enforces RBAC. */
export function UserProvider({ user, children }: { user: SessionUser; children: ReactNode }) {
  return <UserContext.Provider value={user}>{children}</UserContext.Provider>;
}

export function useUser(): SessionUser {
  const user = useContext(UserContext);
  if (!user) throw new Error("useUser outside UserProvider");
  return user;
}

export function useHasRole(...roles: string[]): boolean {
  return useUser().roles.some((r) => roles.includes(r));
}
