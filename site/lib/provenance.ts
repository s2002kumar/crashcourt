// Where a value on a page came from: a harness run or a fixture. Batch and result records say which,
// and everything the pages render carries it from them. The demo banner keys off this and nothing else:
// not the route, not a build flag, not the environment.

import type { BatchRecord } from "./schema/types.gen.ts";

export type Provenance = BatchRecord["provenance"];

/** Anything a page renders carries the provenance of the records it came from. */
export interface Sourced {
  readonly provenance: Provenance;
}

/** True when any source is a fixture, so the page must say it shows demo data. */
export function showsFixture(sources: readonly Sourced[]): boolean {
  return sources.some((s) => s.provenance === "fixture");
}

/** The provenance of something built from several sources: a fixture if any of them is one. */
export function combined(sources: readonly Sourced[]): Provenance {
  return showsFixture(sources) ? "fixture" : "run";
}

/** The attribute that marks the demo banner; the release guard fails a build whose output holds it. */
export const DEMO_BANNER_ATTRIBUTE = "data-demo-banner";
