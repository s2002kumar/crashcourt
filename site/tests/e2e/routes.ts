// One page per experience, as this build exported it: a dynamic route's first page comes from out/,
// so the list follows whatever artifacts the build read.

import { firstPage } from "../../scripts/exported.ts";

export const ROUTES: readonly { name: string; path: string }[] = [
  { name: "overview", path: "/" },
  { name: "matrix", path: "/matrix/" },
  { name: "failure explorer", path: firstPage("/trials/[cell]/[trial]") },
  { name: "lineage", path: firstPage("/lineage/[trial]/[thread]") },
  { name: "finding", path: firstPage("/findings/[id]") },
  { name: "compare", path: firstPage("/compare/[scenario]") },
  { name: "methodology", path: "/methodology/" },
];
