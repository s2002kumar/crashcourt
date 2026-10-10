// Ajv validation of harness artifacts against the frozen schemas. It runs at build time only (server
// components and scripts); nothing here reaches the browser.

import Ajv2020, { type ErrorObject } from "ajv/dist/2020.js";

import { batchSchema, eventSchema, manifestSchema, resultSchema } from "./schemas.gen.ts";
import type { BatchRecord, Event, Manifest, ResultRecord } from "./types.gen.ts";

/**
 * An event as the site holds it. The nanosecond clocks are exact bigints: wall_ns is about 1.8e18,
 * far past Number.MAX_SAFE_INTEGER, so a plain JSON number would round it.
 */
export type LoadedEvent = Omit<Event, "mono_ns" | "wall_ns"> & { mono_ns: bigint; wall_ns: bigint };

export class ArtifactError extends Error {
  readonly file: string;
  readonly line: number | undefined;
  readonly problems: readonly string[];

  constructor(file: string, line: number | undefined, problems: readonly string[]) {
    super(`${file}${line === undefined ? "" : `:${line}`}: ${problems.join("; ")}`);
    this.name = "ArtifactError";
    this.file = file;
    this.line = line;
    this.problems = problems;
  }
}

/** JSON Schema's "date" (RFC 3339 full-date): YYYY-MM-DD naming a real calendar day. */
export function isDate(text: string): boolean {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!m) return false;
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const parsed = new Date(0);
  parsed.setUTCFullYear(year, month - 1, day); // Date.UTC would read years 0-99 as 1900-1999
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

const ajv = new Ajv2020({ allErrors: true, strict: true });
ajv.addFormat("date", { type: "string", validate: isDate });
const checkEvent = ajv.compile<Event>(eventSchema);
const checkManifest = ajv.compile<Manifest>(manifestSchema);
const checkBatch = ajv.compile<BatchRecord>(batchSchema);
const checkResult = ajv.compile<ResultRecord>(resultSchema);

function describe(errors: readonly ErrorObject[] | null | undefined): string[] {
  return (errors ?? []).map((e) => {
    const where = e.instancePath || "(root)";
    const extra =
      e.keyword === "additionalProperty" || e.keyword === "additionalProperties"
        ? ` '${String((e.params as { additionalProperty?: unknown }).additionalProperty)}'`
        : "";
    return `${where} ${e.message ?? e.keyword}${extra}`;
  });
}

const CLOCKS = new Set(["mono_ns", "wall_ns"]);
const INTEGER_LITERAL = /^-?(0|[1-9]\d*)$/;

type ReviverContext = { source?: string } | undefined;

/** JSON.parse reviver keeping clocks and any integer past 2^53 exact, using the source text. */
function exact(this: unknown, key: string, value: unknown, context?: ReviverContext): unknown {
  if (typeof value !== "number") return value;
  const source = context?.source;
  if (CLOCKS.has(key)) {
    if (source === undefined || !INTEGER_LITERAL.test(source)) {
      throw new TypeError(`${key} must be a JSON integer literal, got ${source ?? String(value)}`);
    }
    return BigInt(source);
  }
  if (
    Number.isInteger(value) &&
    !Number.isSafeInteger(value) &&
    source &&
    INTEGER_LITERAL.test(source)
  ) {
    return BigInt(source);
  }
  return value;
}

function parseJson(text: string, file: string, line?: number): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch (e) {
    throw new ArtifactError(file, line, [`not valid JSON (${(e as Error).message})`]);
  }
}

/** Parses and validates a history file: one JSON event per line (JSON Lines). */
export function parseEvents(text: string, file: string): LoadedEvent[] {
  const lines = text.split("\n");
  if (lines.at(-1) === "") lines.pop();
  return lines.map((raw, i) => {
    const line = i + 1;
    const loose = parseJson(raw, file, line);
    if (!checkEvent(loose)) throw new ArtifactError(file, line, describe(checkEvent.errors));
    try {
      return JSON.parse(raw, exact as (key: string, value: unknown) => unknown) as LoadedEvent;
    } catch (e) {
      throw new ArtifactError(file, line, [(e as Error).message]);
    }
  });
}

/** Parses and validates a run manifest. */
export function parseManifest(text: string, file: string): Manifest {
  const value = parseJson(text, file);
  if (!checkManifest(value))
    throw new ArtifactError(file, undefined, describe(checkManifest.errors));
  return value;
}

/** Parses and validates a batch record, including that only a fixture's batch id starts with DEMO-. */
export function parseBatch(text: string, file: string): BatchRecord {
  const value = parseJson(text, file);
  if (!checkBatch(value)) throw new ArtifactError(file, undefined, describe(checkBatch.errors));
  return value;
}

/**
 * Parses and validates a trial's result record: what each outcome needs (a clause, evidence, a repro,
 * a review) and the DEMO- rule are in the schema; one outcome per invariant is checked here. That a
 * result's manifest hash matches its batch's is a cross-file check, left to the release guard (F-02).
 */
export function parseResult(text: string, file: string): ResultRecord {
  const value = parseJson(text, file);
  if (!checkResult(value)) throw new ArtifactError(file, undefined, describe(checkResult.errors));
  // The one rule of the outcome table JSON Schema cannot express: one outcome per invariant.
  const seen = new Set<string>();
  const repeated = new Set<string>();
  for (const { invariant } of value.results) (seen.has(invariant) ? repeated : seen).add(invariant);
  if (repeated.size > 0) {
    throw new ArtifactError(file, undefined, [
      `one outcome per invariant; repeated: ${[...repeated].sort().join(", ")}`,
    ]);
  }
  return value;
}
