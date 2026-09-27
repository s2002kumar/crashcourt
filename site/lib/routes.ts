// Static export needs at least one concrete page per dynamic route. Until artifacts exist for a route,
// it renders a single empty-state page at this segment instead of inventing ids.

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
