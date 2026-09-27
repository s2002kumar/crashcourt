import type { NextConfig } from "next";

// A static export: no server, no API routes, no runtime data fetching (spec section 17, rule 1).
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  // Source maps only for the bundle measurement (scripts/measure-bundles.ts), never in release builds.
  productionBrowserSourceMaps: process.env.CRASHCOURT_SOURCEMAPS === "1",
};

export default config;
