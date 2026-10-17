"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Main navigation; the current section is marked for assistive technology and visually. */
export function NavLinks({ links }: { links: { href: string; label: string }[] }) {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-wrap gap-3">
      {links.map((n) => {
        const current = path === n.href || path.startsWith(`${n.href}/`);
        return (
          <Link
            key={n.href}
            href={n.href}
            aria-current={current ? "page" : undefined}
            className={`rounded px-2 py-1 hover:bg-surface-2 ${current ? "bg-surface-2 font-semibold underline underline-offset-4" : ""}`}
          >
            {n.label}
          </Link>
        );
      })}
    </nav>
  );
}
