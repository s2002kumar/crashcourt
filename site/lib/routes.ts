// The ids each dynamic route exports, read from validated artifacts (lib/batches.ts). A static export
// needs at least one concrete page per dynamic route, so a route with no ids exports a single
// empty-state page at the segment `none` instead of inventing one.

import { comparisons, findings, threadsOf, trialKey, type Batches } from "./batches.ts";

export const EMPTY_SEGMENT = "none";

/** The params a dynamic route exports: the artifact ids when there are any, else the empty state. */
export function paramsOrEmpty<K extends string>(
  keys: readonly K[],
  params: readonly Record<K, string>[],
): Record<K, string>[] {
  if (params.length > 0) return [...params];
  return [Object.fromEntries(keys.map((k) => [k, EMPTY_SEGMENT])) as Record<K, string>];
}

export function isEmptySegment(...segments: string[]): boolean {
  return segments.every((s) => s === EMPTY_SEGMENT);
}

export const trialParams = (all: Batches): { cell: string; trial: string }[] =>
  all.trials.map((t) => ({ cell: t.cell, trial: String(t.trial) }));

export const lineageParams = (all: Batches): { trial: string; thread: string }[] =>
  all.trials.flatMap((t) => threadsOf(t).map((thread) => ({ trial: trialKey(t), thread })));

export const findingParams = (all: Batches): { id: string }[] =>
  findings(all).map((f) => ({ id: f.id }));

export const compareParams = (all: Batches): { scenario: string }[] =>
  comparisons(all).map((c) => ({ scenario: c.scenario }));

/** A page's path from its segments, each encoded, with the export's trailing slash. */
export function pagePath(...segments: string[]): string {
  return `/${segments.map(encodeURIComponent).join("/")}/`;
}

/** The first page a dynamic route exports in this build: an artifact's page, or the empty state. */
export function firstPages(all: Batches): Readonly<Record<string, string>> {
  const first = <K extends string>(keys: readonly K[], params: readonly Record<K, string>[]) =>
    paramsOrEmpty(keys, params)[0]!;
  const trial = first(["cell", "trial"], trialParams(all));
  const lineage = first(["trial", "thread"], lineageParams(all));
  const finding = first(["id"], findingParams(all));
  const compare = first(["scenario"], compareParams(all));
  return {
    "/trials/[cell]/[trial]": pagePath("trials", trial.cell, trial.trial),
    "/lineage/[trial]/[thread]": pagePath("lineage", lineage.trial, lineage.thread),
    "/findings/[id]": pagePath("findings", finding.id),
    "/compare/[scenario]": pagePath("compare", compare.scenario),
  };
}
