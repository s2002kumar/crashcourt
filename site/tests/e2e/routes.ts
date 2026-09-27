// Every page the static export produces, as the explorer's users reach them.
export const ROUTES: readonly { name: string; path: string }[] = [
  { name: "overview", path: "/" },
  { name: "matrix", path: "/matrix/" },
  { name: "failure explorer", path: "/trials/none/none/" },
  { name: "lineage", path: "/lineage/none/none/" },
  { name: "finding", path: "/findings/none/" },
  { name: "compare", path: "/compare/none/" },
  { name: "methodology", path: "/methodology/" },
];
