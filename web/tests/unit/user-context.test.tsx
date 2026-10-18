// @vitest-environment jsdom
import { cleanup, render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UserProvider, useHasRole, useUser } from "@/components/user-context";
import type { SessionUser } from "@/lib/session";

afterEach(cleanup);

const user: SessionUser = {
  username: "ana.analyst",
  roles: ["analyst"],
  tenantId: "5d52f1a8-7b91-5150-919c-ce3a391e5ea8",
};

const wrapper = ({ children }: { children: ReactNode }) => (
  <UserProvider user={user}>{children}</UserProvider>
);

describe("UserProvider", () => {
  it("gives components the signed-in user", () => {
    function Name() {
      return <span>{useUser().username}</span>;
    }
    render(<Name />, { wrapper });
    expect(screen.getByText("ana.analyst")).toBeTruthy();
  });

  it("answers role checks for role-aware UI", () => {
    expect(renderHook(() => useHasRole("analyst"), { wrapper }).result.current).toBe(true);
    expect(renderHook(() => useHasRole("admin", "analyst"), { wrapper }).result.current).toBe(true);
    expect(renderHook(() => useHasRole("admin"), { wrapper }).result.current).toBe(false);
    expect(renderHook(() => useHasRole(), { wrapper }).result.current).toBe(false);
  });

  it("refuses to be used outside the provider", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => renderHook(() => useUser())).toThrow("useUser outside UserProvider");
    quiet.mockRestore();
  });
});
