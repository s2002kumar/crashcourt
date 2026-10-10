// The seven experiences of the evidence explorer in the order a reader meets them.

export interface Experience {
  readonly name: string;
  readonly route: string;
  /** False for a route that stays unlinked: compare, until two targets have validated runs. */
  readonly linked: boolean;
  readonly summary: string;
}

export const EXPERIENCES: readonly Experience[] = [
  {
    name: "Overview",
    route: "/",
    linked: true,
    summary: "The latest validated run, its controls and verdict counts, and the flagship finding.",
  },
  {
    name: "Fault matrix",
    route: "/matrix",
    linked: true,
    summary: "Workload and fault against target and durability; every cell opens its trials.",
  },
  {
    name: "Failure explorer",
    route: "/trials/[cell]/[trial]",
    linked: true,
    summary: "Synchronized lanes for client, workers, database commits, and the effect sink.",
  },
  {
    name: "Checkpoint lineage",
    route: "/lineage/[trial]/[thread]",
    linked: true,
    summary: "The checkpoint graph of one thread: forks, the chosen head, orphaned branches.",
  },
  {
    name: "Finding",
    route: "/findings/[id]",
    linked: true,
    summary: "One finding: the contract, the verdict, the minimal schedule, and the repro.",
  },
  {
    name: "Compare systems",
    route: "/compare/[scenario]",
    linked: false,
    summary: "One scenario across runtimes. Hidden until two targets have validated runs.",
  },
  {
    name: "Methodology",
    route: "/methodology",
    linked: true,
    summary: "How we know the harness did not create the bug, and what is not claimed.",
  },
];

export function experience(name: string): Experience {
  const found = EXPERIENCES.find((e) => e.name === name);
  if (!found) throw new Error(`unknown experience: ${name}`);
  return found;
}
