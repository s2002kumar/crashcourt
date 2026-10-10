// Presentation tokens for verdicts, actors, and faults: glyph, label, and CSS color variable.
//
// Apart from the schema modules generated from schemas/v1, this is the only site source file that
// spells a verdict name, holds the list of verdicts, or names the Verdict type. Other code gets a verdict
// from a validated artifact and renders it through `verdictToken`, or lists the vocabulary through
// `verdictLegend`; an outcome from a result record renders through `outcomeToken`, which also covers the
// two outcomes that are not verdicts (a violation candidate, and no anomaly with no clause).
// scripts/guard-verdicts.ts fails the build on any of those elsewhere; its header says what it cannot
// see.

import type { Outcome } from "./schema/types.gen.ts";

const VERDICTS = ["PASS", "EXPECTED", "GAP", "VIOLATION", "INVALID"] as const;
export type Verdict = (typeof VERDICTS)[number];

/** Props for a component that renders one verdict it was given. */
export interface VerdictProp {
  readonly verdict: Verdict;
}

export type VerdictGlyphName = "check" | "tilde" | "diamond" | "cross" | "empty";

export interface VerdictToken {
  readonly label: string;
  readonly glyph: VerdictGlyphName;
  /** The character the glyph draws, for plain-text contexts. */
  readonly char: string;
  readonly color: string;
  readonly meaning: string;
}

const VERDICT_TOKENS: Readonly<Record<Verdict, VerdictToken>> = {
  PASS: {
    label: "PASS",
    glyph: "check",
    char: "✓",
    color: "var(--verdict-pass)",
    meaning: "A documented guarantee applies and no anomaly was observed.",
  },
  EXPECTED: {
    label: "EXPECTED",
    glyph: "tilde",
    char: "~",
    color: "var(--verdict-expected)",
    meaning: "An anomaly occurred that a documented clause permits under the conditions that held.",
  },
  GAP: {
    label: "GAP",
    glyph: "diamond",
    char: "◇",
    color: "var(--verdict-gap)",
    meaning: "No documented clause covers the behavior. It is characterized, not called a bug.",
  },
  VIOLATION: {
    label: "VIOLATION",
    glyph: "cross",
    char: "✕",
    color: "var(--verdict-violation)",
    meaning:
      "A documented guarantee was broken, with oracle evidence, a standalone repro, and human review.",
  },
  INVALID: {
    label: "INVALID",
    glyph: "empty",
    char: "∅",
    color: "var(--verdict-invalid)",
    meaning: "A control, completeness, or environment check failed, so the trial says nothing.",
  },
};

export function verdictToken(verdict: Verdict): VerdictToken {
  return VERDICT_TOKENS[verdict];
}

/** Every verdict with its token, in display order: the vocabulary, never a result. */
export function verdictLegend(): readonly (VerdictProp & { readonly token: VerdictToken })[] {
  return VERDICTS.map((verdict) => ({ verdict, token: VERDICT_TOKENS[verdict] }));
}

// The two outcomes that are not verdicts. Neither borrows a verdict's look: no verdict color (they are
// drawn in muted ink), a dashed edge, and a glyph no verdict uses, so a candidate never reads as a
// violation and an unspecified outcome never reads as a pass.

export type OtherGlyphName = "query" | "dash";

export interface OtherOutcomeToken {
  readonly label: string;
  readonly glyph: OtherGlyphName;
  readonly char: string;
  readonly meaning: string;
}

const OTHER_TOKENS: Readonly<Record<Exclude<Outcome, Verdict>, OtherOutcomeToken>> = {
  VIOLATION_CANDIDATE: {
    label: "CANDIDATE",
    glyph: "query",
    char: "?",
    meaning:
      "A documented guarantee forbids the anomaly. It is not a violation until a standalone repro and human review confirm it.",
  },
  NO_ANOMALY_UNSPECIFIED: {
    label: "UNSPECIFIED",
    glyph: "dash",
    char: "–",
    meaning: "No anomaly was observed, and no documented clause covers the case. It is not a pass.",
  },
};

/** The two outcomes that are not verdicts, with their tokens: vocabulary, never a result. */
export function otherOutcomeLegend(): readonly (OutcomeProp & {
  readonly token: OtherOutcomeToken;
})[] {
  return Object.entries(OTHER_TOKENS).map(([outcome, token]) => ({
    outcome: outcome as Outcome,
    token,
  }));
}

/** Props for a component that renders one outcome a result record carries. */
export interface OutcomeProp {
  readonly outcome: Outcome;
}

/** An outcome's token: a verdict's own, or one of the two outcomes that are not verdicts. */
export type OutcomeToken =
  | { readonly kind: "verdict"; readonly verdict: Verdict; readonly token: VerdictToken }
  | { readonly kind: "other"; readonly token: OtherOutcomeToken };

export function outcomeToken(outcome: Outcome): OutcomeToken {
  if (outcome in VERDICT_TOKENS) {
    const verdict = outcome as Verdict;
    return { kind: "verdict", verdict, token: VERDICT_TOKENS[verdict] };
  }
  return { kind: "other", token: OTHER_TOKENS[outcome as Exclude<Outcome, Verdict>] };
}

export const ACTORS = ["client", "worker-a", "worker-b", "database", "sink"] as const;
export type Actor = (typeof ACTORS)[number];

export interface ActorToken {
  readonly label: string;
  readonly short: string;
  readonly color: string;
}

const ACTOR_TOKENS: Readonly<Record<Actor, ActorToken>> = {
  client: { label: "Client", short: "C", color: "var(--actor-client)" },
  "worker-a": { label: "Worker A", short: "A", color: "var(--actor-worker-a)" },
  "worker-b": { label: "Worker B", short: "B", color: "var(--actor-worker-b)" },
  database: { label: "Database", short: "DB", color: "var(--actor-database)" },
  sink: { label: "Effect sink", short: "S", color: "var(--actor-sink)" },
};

export function actorToken(actor: Actor): ActorToken {
  return ACTOR_TOKENS[actor];
}

export const FAULT_MARKS = [
  "sigkill",
  "sigstop",
  "sigcont",
  "toxic",
  "db-restart",
  "barrier",
] as const;
export type FaultMark = (typeof FAULT_MARKS)[number];

export interface FaultToken {
  readonly label: string;
  readonly meaning: string;
}

const FAULT_TOKENS: Readonly<Record<FaultMark, FaultToken>> = {
  sigkill: { label: "SIGKILL", meaning: "The process is killed with no chance to clean up." },
  sigstop: { label: "SIGSTOP", meaning: "The process is frozen in place." },
  sigcont: { label: "SIGCONT", meaning: "A frozen process resumes." },
  toxic: { label: "Network toxic", meaning: "A proxy holds, delays, or resets database traffic." },
  "db-restart": { label: "Database restart", meaning: "The database shuts down and restarts." },
  barrier: { label: "Barrier", meaning: "Workers wait at a failpoint until released together." },
};

export function faultToken(mark: FaultMark): FaultToken {
  return FAULT_TOKENS[mark];
}
