import { ActorSwatch, FaultGlyph } from "@/components/glyphs";
import { ACTORS, FAULT_MARKS, actorToken, faultToken } from "@/lib/tokens";

export function ActorLegend() {
  return (
    <ul className="grid gap-2 text-sm">
      {ACTORS.map((a) => (
        <li key={a} className="flex items-center gap-2.5">
          <ActorSwatch actor={a} />
          <span>{actorToken(a).label}</span>
        </li>
      ))}
    </ul>
  );
}

export function FaultLegend() {
  return (
    <dl className="grid gap-3">
      {FAULT_MARKS.map((m) => (
        <div key={m} className="grid grid-cols-[1.25rem_1fr] items-start gap-x-2.5">
          <dt className="contents">
            <FaultGlyph mark={m} className="mt-0.5" />
            <span className="readout text-xs tracking-wide">{faultToken(m).label}</span>
          </dt>
          <dd className="col-start-2 text-sm text-ink-muted">{faultToken(m).meaning}</dd>
        </div>
      ))}
    </dl>
  );
}
