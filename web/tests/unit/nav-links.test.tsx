// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NavLinks } from "@/components/nav-links";

const nav = vi.hoisted(() => ({ path: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.path }));

afterEach(cleanup);

const LINKS = [
  { href: "/queue", label: "Queue" },
  { href: "/replies", label: "Replies" },
  { href: "/promises", label: "Promises" },
];

describe("NavLinks", () => {
  it("renders a labelled main navigation with one link per section", () => {
    nav.path = "/";
    render(<NavLinks links={LINKS} />);
    const links = screen.getByRole("navigation", { name: "Main" }).querySelectorAll("a");
    expect(Array.from(links, (a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Queue", "/queue"],
      ["Replies", "/replies"],
      ["Promises", "/promises"],
    ]);
    expect(screen.queryByRole("link", { current: "page" })).toBeNull();
  });

  it("marks the current section, including its sub-pages", () => {
    nav.path = "/replies/abc";
    render(<NavLinks links={LINKS} />);
    const current = screen.getByRole("link", { current: "page" });
    expect(current.textContent).toBe("Replies");
    expect(screen.getByRole("link", { name: "Queue" }).getAttribute("aria-current")).toBeNull();
  });

  it("does not treat a shared prefix as the same section", () => {
    nav.path = "/queue-archive";
    render(<NavLinks links={LINKS} />);
    expect(screen.queryByRole("link", { current: "page" })).toBeNull();
  });

  it("marks an exact match", () => {
    nav.path = "/promises";
    render(<NavLinks links={LINKS} />);
    expect(screen.getByRole("link", { current: "page" }).textContent).toBe("Promises");
  });
});
