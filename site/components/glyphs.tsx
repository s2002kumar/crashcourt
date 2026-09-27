// Glyphs for verdicts, faults, and actors. They are drawn as SVG rather than typed as Unicode so they look
// the same on every platform and font. Each is decorative (aria-hidden); a text label always sits beside it.

import type { Actor, FaultMark, Verdict, VerdictGlyphName } from "@/lib/tokens";
import { actorToken, verdictToken } from "@/lib/tokens";

interface GlyphProps {
  readonly size?: number;
  readonly className?: string;
}

const VERDICT_PATHS: Record<VerdictGlyphName, React.ReactNode> = {
  check: <path d="M3.5 8.5l3 3 6-7" fill="none" />,
  tilde: <path d="M2.5 9.5c1.6-2.6 3.4-2.6 5 0s3.4 2.6 5 0" fill="none" />,
  diamond: <path d="M8 2.5L13.5 8 8 13.5 2.5 8z" fill="none" />,
  cross: <path d="M4 4l8 8M12 4l-8 8" fill="none" />,
  empty: (
    <>
      <circle cx="8" cy="8" r="4.5" fill="none" />
      <path d="M12.5 3.5l-9 9" fill="none" />
    </>
  ),
};

export function VerdictGlyph({ verdict, size = 16, className }: GlyphProps & { verdict: Verdict }) {
  const token = verdictToken(verdict);
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      width={size}
      height={size}
      className={className}
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ color: token.color }}
    >
      {VERDICT_PATHS[token.glyph]}
    </svg>
  );
}

// Faults are the accent's job: yellow fill with a dark edge, like a crash-test marker. Shape, not color,
// tells them apart.
const FAULT_PATHS: Record<FaultMark, React.ReactNode> = {
  sigkill: <path d="M2.5 3.5h11L8 13.5z" />,
  sigstop: (
    <>
      <rect x="3.5" y="3" width="3.25" height="10" rx="0.5" />
      <rect x="9.25" y="3" width="3.25" height="10" rx="0.5" />
    </>
  ),
  sigcont: <path d="M4 2.75v10.5L13 8z" />,
  toxic: <path d="M1.5 9.5l3-4 3 5 3-5 3 4" fill="none" strokeWidth={2.25} />,
  "db-restart": (
    <>
      <path d="M12.4 6.2A4.75 4.75 0 1 0 12.75 9" fill="none" strokeWidth={2} />
      <path d="M13.5 2.5v4.25H9.25z" />
    </>
  ),
  barrier: (
    <>
      <rect x="2.5" y="2.5" width="2.5" height="11" rx="0.5" />
      <rect x="11" y="2.5" width="2.5" height="11" rx="0.5" />
      <rect x="5" y="6.75" width="6" height="2.5" />
    </>
  ),
};

export function FaultGlyph({ mark, size = 16, className }: GlyphProps & { mark: FaultMark }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      width={size}
      height={size}
      className={className}
      fill="var(--accent)"
      stroke="var(--accent-edge)"
      strokeWidth={1}
      strokeLinejoin="round"
    >
      {FAULT_PATHS[mark]}
    </svg>
  );
}

export function ActorSwatch({ actor, className }: { actor: Actor; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-2.5 w-4 shrink-0 rounded-[1px] ${className ?? ""}`}
      style={{ background: actorToken(actor).color }}
    />
  );
}

/** The crash-test target: a quartered disc, two quadrants in the accent. */
export function BrandMark({ size = 22, className }: GlyphProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="10.25" fill="var(--ink)" />
      <path d="M12 12V1.75A10.25 10.25 0 0 0 1.75 12z" fill="var(--accent)" />
      <path d="M12 12v10.25A10.25 10.25 0 0 0 22.25 12z" fill="var(--accent)" />
      <circle cx="12" cy="12" r="10.25" fill="none" stroke="var(--accent-edge)" strokeWidth="1.5" />
    </svg>
  );
}
