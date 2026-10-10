import Link from "next/link";

import { EmptyState, Panel, Readout } from "@/components/panel";
import { VerdictBadge } from "@/components/verdict";
import { EXPERIENCES } from "@/lib/experiences";
import { verdictLegend } from "@/lib/tokens";

const NAMEPLATE: readonly [label: string, value: string][] = [
  ["Status", "Pre-results"],
  ["First target", "LangGraph checkpointers on Postgres"],
  ["Faults", "Legal only: signals, network, restarts"],
  ["Oracle", "Effect ledger, process histories, database snapshot"],
];

export default function OverviewPage() {
  return (
    <div className="grid gap-12">
      <header className="grid gap-8 border-b border-rule pb-10 lg:grid-cols-12 lg:gap-6">
        <div className="grid content-start gap-6 lg:col-span-8">
          <p className="label">Evidence explorer</p>
          <h1 className="display max-w-[20ch] text-4xl text-ink sm:text-6xl">
            An open-source conformance and reliability lab for durable agent execution.
          </h1>
          <p className="max-w-[64ch] text-lg text-ink-muted">
            Crashcourt will crash, freeze, and partition the persistence layers under agent
            runtimes, starting with LangGraph checkpointers on real Postgres, then judge each
            outcome against the runtime&rsquo;s documented guarantees and an independent record of
            real-world effects.
          </p>
        </div>
        <aside aria-label="About this lab" className="lg:col-span-4 lg:self-end">
          <dl className="panel grid gap-4 p-5">
            {NAMEPLATE.map(([label, value]) => (
              <div key={label} className="grid gap-1">
                <dt className="label">{label}</dt>
                <dd className="text-sm text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        </aside>
      </header>

      <Panel label="Latest validated run">
        <dl className="grid grid-cols-2 gap-5 sm:grid-cols-4">
          <Readout label="Batch" value={null} />
          <Readout label="Date" value={null} />
          <Readout label="Runtime" value={null} />
          <Readout label="Controls passing" value={null} />
        </dl>
        <dl
          aria-label="Counts by verdict"
          className="mt-6 grid grid-cols-2 gap-x-5 gap-y-4 border-t border-rule pt-5 sm:grid-cols-3 lg:grid-cols-5"
        >
          {verdictLegend().map(({ verdict }) => (
            <div key={verdict} className="grid justify-items-start gap-2">
              <dt>
                <VerdictBadge verdict={verdict} />
              </dt>
              <dd className="readout text-lg">
                <span aria-hidden="true" className="text-ink-faint">
                  &mdash;
                </span>
                <span className="sr-only">no count</span>
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-6 border-t border-rule pt-4 text-sm text-ink-muted">
          Pre-results. No batch has been validated, so there is nothing to count yet.
        </p>
      </Panel>

      <div className="grid gap-6 lg:grid-cols-12">
        <Panel label="Crash test" className="lg:col-span-7">
          <EmptyState>
            Plays one real trial from its merged history: two workers, the fault, the lineage split,
            and the effect ledger. It appears with the first validated batch.
          </EmptyState>
        </Panel>
        <Panel label="Flagship finding" className="lg:col-span-5">
          <EmptyState>No finding has been published.</EmptyState>
        </Panel>
      </div>

      <section aria-labelledby="trust" className="grid gap-3 border-y border-rule py-8">
        <h2 id="trust" className="display text-2xl text-ink sm:text-3xl">
          How do we know we didn&rsquo;t create the bug?
        </h2>
        <p className="max-w-[68ch] text-ink-muted">
          Controls, legal-only faults, and an oracle the target does not control.{" "}
          <Link href="/methodology/" className="text-ink underline">
            Read the methodology
          </Link>
        </p>
      </section>

      <section aria-labelledby="experiences" className="grid gap-5">
        <h2 id="experiences" className="label">
          Experiences
        </h2>
        <ol className="divide-y divide-rule rounded-panel border border-rule bg-panel">
          {EXPERIENCES.map((e) => (
            <li
              key={e.route}
              className="grid gap-1.5 px-5 py-4 md:grid-cols-[13rem_15rem_1fr] md:items-baseline md:gap-6"
            >
              <h3 className="font-semibold text-ink">
                {e.href ? (
                  <Link href={e.href} className="no-underline hover:underline">
                    {e.name}
                  </Link>
                ) : (
                  e.name
                )}
              </h3>
              <p className="readout text-xs text-ink-faint">{e.route}</p>
              <p className="text-sm text-ink-muted">{e.summary}</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
