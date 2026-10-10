import { OtherOutcomeGlyph, VerdictGlyph } from "@/components/glyphs";
import type { OutcomeProp, VerdictProp } from "@/lib/tokens";
import { otherOutcomeLegend, outcomeToken, verdictLegend, verdictToken } from "@/lib/tokens";

/**
 * A verdict as the site shows it everywhere: glyph, label, and color together. The color goes on the
 * glyph, the edge, and a light tint; the label stays in ink so it reads at full contrast.
 */
export function VerdictBadge({ verdict }: VerdictProp) {
  const token = verdictToken(verdict);
  return (
    <span
      className="readout inline-flex items-center gap-1.5 rounded-panel border px-1.5 py-0.5 text-xs font-medium tracking-wide text-ink"
      style={{
        borderColor: `color-mix(in oklab, ${token.color} 55%, transparent)`,
        background: `color-mix(in oklab, ${token.color} var(--verdict-tint), transparent)`,
      }}
    >
      <VerdictGlyph verdict={verdict} size={14} />
      {token.label}
    </span>
  );
}

/** Every verdict with its meaning. Renders the vocabulary, never a result. */
export function VerdictLegend() {
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-1">
      {verdictLegend().map(({ verdict, token }) => (
        <div key={verdict} className="grid gap-1">
          <dt>
            <VerdictBadge verdict={verdict} />
          </dt>
          <dd className="text-sm text-ink-muted">{token.meaning}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * An outcome from a result record: a verdict renders as its badge; the two outcomes that are not
 * verdicts get a dashed edge, no tint, and a muted glyph, so neither reads as a verdict.
 */
export function OutcomeBadge({ outcome }: OutcomeProp) {
  const t = outcomeToken(outcome);
  if (t.kind === "verdict") return <VerdictBadge verdict={t.verdict} />;
  return (
    <span
      title={t.token.meaning}
      className="readout inline-flex items-center gap-1.5 rounded-panel border border-dashed border-rule-strong px-1.5 py-0.5 text-xs font-medium tracking-wide text-ink"
    >
      <OtherOutcomeGlyph glyph={t.token.glyph} size={14} />
      {t.token.label}
    </span>
  );
}

/** The two outcomes that are not verdicts, with their meanings. Renders the vocabulary, never a result. */
export function OtherOutcomeLegend() {
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-1">
      {otherOutcomeLegend().map(({ outcome, token }) => (
        <div key={token.label} className="grid gap-1">
          <dt>
            <OutcomeBadge outcome={outcome} />
          </dt>
          <dd className="text-sm text-ink-muted">{token.meaning}</dd>
        </div>
      ))}
    </dl>
  );
}
