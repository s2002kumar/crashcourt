import { VerdictGlyph } from "@/components/glyphs";
import type { Verdict } from "@/lib/tokens";
import { VERDICTS, verdictToken } from "@/lib/tokens";

/**
 * A verdict as the site shows it everywhere: glyph, label, and color together. The color goes on the
 * glyph, the edge, and a light tint; the label stays in ink so it reads at full contrast.
 */
export function VerdictBadge({ verdict }: { verdict: Verdict }) {
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
      {VERDICTS.map((v) => (
        <div key={v} className="grid gap-1">
          <dt>
            <VerdictBadge verdict={v} />
          </dt>
          <dd className="text-sm text-ink-muted">{verdictToken(v).meaning}</dd>
        </div>
      ))}
    </dl>
  );
}
