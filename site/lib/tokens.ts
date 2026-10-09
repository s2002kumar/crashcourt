// Presentation tokens for verdicts, actors, and faults: glyph, label, and CSS color variable.
//
// This is the only site file that spells a verdict name, holds the list of verdicts, or names the
// Verdict type. Other code gets a verdict from a validated artifact and renders it through
// `verdictToken`, or lists the vocabulary through `verdictLegend`. scripts/guard-verdicts.ts fails the
// build when a quoted verdict name, the list, or the type appears anywhere else. It reads text, so it
// cannot see a name built at run time; it is a tripwire that review backs up, not a proof.

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
