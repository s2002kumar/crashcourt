import Link from "next/link";

import { BrandMark } from "@/components/glyphs";
import { NavLinks } from "@/components/nav-links";
import { Shortcuts } from "@/components/shortcuts";
import { ThemeToggle } from "@/components/theme-toggle";

export const NAV: readonly { href: string; label: string }[] = [
  { href: "/", label: "Overview" },
  { href: "/matrix/", label: "Matrix" },
  { href: "/methodology/", label: "Methodology" },
];

export function SkipLink() {
  return (
    <a
      href="#main"
      className="label sr-only z-50 bg-accent px-3 py-2 text-accent-ink focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
    >
      Skip to content
    </a>
  );
}

export function SiteHeader() {
  return (
    <header className="border-b border-rule bg-bg">
      <div className="mx-auto flex max-w-page flex-wrap items-center gap-x-8 gap-y-3 px-4 py-3 md:px-6">
        <Link href="/" className="flex items-center gap-2.5 rounded-[2px] text-ink no-underline">
          <BrandMark />
          <span className="font-mono text-sm font-semibold tracking-[0.16em] uppercase">
            Crashcourt
          </span>
        </Link>
        <NavLinks links={NAV} />
        <div className="ml-auto flex items-center gap-2">
          <Shortcuts />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-rule">
      <div className="mx-auto grid max-w-page gap-2 px-4 py-8 text-sm text-ink-muted md:grid-cols-2 md:px-6">
        <p>Crashcourt evidence explorer. It renders harness artifacts and computes no verdicts.</p>
        <p className="readout text-xs md:text-right">schema v1 &middot; pre-results</p>
      </div>
    </footer>
  );
}
