import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import { SiteFooter, SiteHeader, SkipLink } from "@/components/site-chrome";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

import "./globals.css";

// Fonts come from the lockfile (Fontsource packages), not from a network fetch at build time.
const instrument = localFont({
  src: "../node_modules/@fontsource-variable/instrument-sans/files/instrument-sans-latin-wdth-normal.woff2",
  weight: "400 700",
  declarations: [{ prop: "font-stretch", value: "75% 100%" }],
  variable: "--font-instrument",
  display: "swap",
  adjustFontFallback: "Arial",
});

const jetbrains = localFont({
  src: "../node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2",
  weight: "100 800",
  variable: "--font-jetbrains",
  display: "swap",
  adjustFontFallback: false,
});

export const metadata: Metadata = {
  title: { default: "Crashcourt", template: "%s · Crashcourt" },
  description:
    "An open-source conformance and reliability lab for durable agent execution. Evidence explorer.",
};

export const viewport: Viewport = {
  colorScheme: "dark light",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0b0e12" },
    { media: "(prefers-color-scheme: light)", color: "#f4f4f0" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${instrument.variable} ${jetbrains.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="flex min-h-dvh flex-col">
        <SkipLink />
        <SiteHeader />
        <main
          id="main"
          tabIndex={-1}
          className="mx-auto w-full max-w-page flex-1 px-4 pt-10 outline-none md:px-6 md:pt-14"
        >
          {children}
        </main>
        <SiteFooter />
      </body>
    </html>
  );
}
