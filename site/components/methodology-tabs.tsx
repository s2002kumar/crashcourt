"use client";

import * as Tabs from "@radix-ui/react-tabs";

import { EmptyState } from "@/components/panel";

const SECTIONS: readonly { id: string; label: string; body: React.ReactNode }[] = [
  {
    id: "trust",
    label: "Trust",
    body: (
      <ul className="grid max-w-[68ch] list-disc gap-3 pl-5 text-ink-muted marker:text-ink-faint">
        <li>
          Faults are legal only: signals, network faults through a proxy, and database restarts.
          Nothing edits the target&rsquo;s rows or patches its code.
        </li>
        <li>
          The judge is an oracle the target does not control: an effect ledger, per-process
          histories, and a direct database snapshot. What the target reports about itself is a claim
          to check.
        </li>
        <li>A batch with any failed control is invalid in its entirety.</li>
        <li>
          A violation needs a cited clause, oracle evidence, a standalone repro that imports none of
          this project&rsquo;s code, and human review. Without a clause, the result is a gap.
        </li>
      </ul>
    ),
  },
  {
    id: "controls",
    label: "Controls",
    body: (
      <EmptyState>
        Each batch&rsquo;s controls, with their results and trials, appear here.
      </EmptyState>
    ),
  },
  {
    id: "clauses",
    label: "Clauses",
    body: (
      <EmptyState>
        The clause registry: each documented guarantee with its source, retrieval date, and hash.
      </EmptyState>
    ),
  },
  {
    id: "statistics",
    label: "Statistics",
    body: (
      <EmptyState>
        Counts with intervals or bounds, never rates without their denominator.
      </EmptyState>
    ),
  },
  {
    id: "limitations",
    label: "Limitations",
    body: <EmptyState>What a batch does not cover, stated before its results.</EmptyState>,
  },
];

export function MethodologyTabs() {
  return (
    <Tabs.Root defaultValue="trust" className="panel">
      <Tabs.List
        aria-label="Methodology sections"
        className="flex flex-wrap border-b border-rule px-2"
      >
        {SECTIONS.map((s) => (
          <Tabs.Trigger
            key={s.id}
            value={s.id}
            className="label -mb-px border-b-2 border-transparent px-3 py-3 text-ink-muted hover:text-ink data-[state=active]:border-ink data-[state=active]:text-ink"
          >
            {s.label}
          </Tabs.Trigger>
        ))}
      </Tabs.List>
      {SECTIONS.map((s) => (
        <Tabs.Content key={s.id} value={s.id} className="p-6">
          {s.body}
        </Tabs.Content>
      ))}
    </Tabs.Root>
  );
}
