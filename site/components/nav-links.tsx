"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function NavLinks({ links }: { links: readonly { href: string; label: string }[] }) {
  const pathname = usePathname();
  const current = (href: string) =>
    href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href);
  return (
    <nav aria-label="Primary">
      <ul className="flex gap-1">
        {links.map(({ href, label }) => (
          <li key={href}>
            <Link
              href={href}
              aria-current={current(href) ? "page" : undefined}
              className="block rounded-[2px] px-2.5 py-1.5 text-sm text-ink-muted no-underline hover:text-ink aria-[current=page]:text-ink aria-[current=page]:shadow-[inset_0_-1px_0_var(--ink)]"
            >
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
