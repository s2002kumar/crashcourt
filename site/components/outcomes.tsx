// A trial's outcomes as its result record states them: one per invariant, with the clause, evidence,
// repro, and note it carries. Nothing is derived; a missing value is a dash.

import { OutcomeBadge } from "@/components/verdict";
import type { InvariantResult } from "@/lib/schema/types.gen";

function Value({ children }: { children: React.ReactNode }) {
  if (children === null || children === undefined) {
    return (
      <>
        <span aria-hidden="true" className="text-ink-faint">
          &mdash;
        </span>
        <span className="sr-only">none</span>
      </>
    );
  }
  return <>{children}</>;
}

export function OutcomeList({ results }: { results: readonly InvariantResult[] }) {
  return (
    <ul className="grid gap-4">
      {results.map((r) => (
        <li
          key={r.invariant}
          className="grid gap-2.5 border-t border-rule pt-4 first:border-0 first:pt-0"
        >
          <div className="flex flex-wrap items-center gap-3">
            <span className="readout text-sm text-ink">{r.invariant}</span>
            <OutcomeBadge outcome={r.outcome} />
          </div>
          <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[6rem_1fr]">
            <dt className="label pt-0.5">Clause</dt>
            <dd className="readout break-all text-ink">
              <Value>{r.clause ?? null}</Value>
            </dd>
            <dt className="label pt-0.5">Evidence</dt>
            <dd className="readout text-ink">
              <Value>
                {r.evidence && r.evidence.length > 0 ? (
                  <ul className="grid gap-0.5">
                    {r.evidence.map((ref) => (
                      <li key={ref} className="break-all">
                        {ref}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </Value>
            </dd>
            <dt className="label pt-0.5">Repro</dt>
            <dd className="readout break-all text-ink">
              <Value>{r.repro ?? null}</Value>
            </dd>
            <dt className="label pt-0.5">Note</dt>
            <dd className="text-ink-muted">
              <Value>{r.reason ?? null}</Value>
            </dd>
          </dl>
        </li>
      ))}
    </ul>
  );
}
