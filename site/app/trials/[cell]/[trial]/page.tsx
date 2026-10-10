import type { Metadata } from "next";
import Link from "next/link";

import { DemoBanner } from "@/components/DemoBanner";
import { ActorLegend, FaultLegend } from "@/components/legends";
import { OutcomeList } from "@/components/outcomes";
import { EmptyState, PageHeader, Panel, Readout } from "@/components/panel";
import { LanesFrame } from "@/components/timeline/lanes-frame";
import { batchOf, evidence, findTrial, threadsOf, trialKey } from "@/lib/batches";
import { isEmptySegment, pagePath, paramsOrEmpty, trialParams } from "@/lib/routes";

type Params = { cell: string; trial: string };

export const dynamicParams = false;

export function generateStaticParams(): Params[] {
  return paramsOrEmpty(["cell", "trial"], trialParams(evidence()));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { cell, trial } = await params;
  return isEmptySegment(cell, trial)
    ? { title: "Failure explorer", robots: { index: false } }
    : { title: `Trial ${cell} #${trial}` };
}

export default async function TrialPage({ params }: { params: Promise<Params> }) {
  const { cell, trial } = await params;
  const all = evidence();
  const found = isEmptySegment(cell, trial) ? undefined : findTrial(all, cell, trial);
  const batch = found ? batchOf(all, found) : undefined;
  const events = found?.merged?.events ?? [];
  // The axis spans the merged history; tick labels are axis labels, not artifact values.
  const spanMs: readonly [number, number] | null =
    events.length > 1 ? [0, Number(events.at(-1)!.mono_ns - events[0]!.mono_ns) / 1e6] : null;
  return (
    <div className="grid gap-10">
      <DemoBanner sources={found && batch ? [found, batch] : []} />
      <PageHeader
        experience="Failure explorer"
        route={found ? `/trials/${cell}/${trial}` : "/trials/[cell]/[trial]"}
        title={found ? `Trial ${cell} #${trial}` : "One trial, step by step"}
      >
        <p>
          Every process on its own lane, faults where they landed, and causal arrows between them,
          played back in the order the checker proved, not the order the clocks say.
        </p>
      </PageHeader>
      <Panel label="Timeline">
        <LanesFrame spanMs={spanMs} />
      </Panel>
      {found && batch ? (
        <div className="grid items-start gap-6 lg:grid-cols-12">
          <Panel label="Outcomes" className="lg:col-span-8">
            <OutcomeList results={found.result.results} />
          </Panel>
          <Panel label="Artifacts" className="lg:col-span-4">
            <dl className="grid gap-4">
              <Readout label="Batch" value={batch.id} />
              <Readout label="Seed" value={events[0]?.seed ?? null} />
            </dl>
            <h3 className="label mt-5 mb-2">Files</h3>
            <ul className="readout grid gap-1 text-xs break-all text-ink">
              <li>{`${found.dir}/result.json`}</li>
              {found.histories.map((h) => (
                <li key={h.file}>{h.file}</li>
              ))}
            </ul>
            <h3 className="label mt-5 mb-2">Lineage</h3>
            <ul className="grid gap-1 text-sm">
              {threadsOf(found).map((thread) => (
                <li key={thread}>
                  <Link
                    href={pagePath("lineage", trialKey(found), thread)}
                    className="readout text-ink"
                  >
                    {thread}
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      ) : null}
      <div className="grid items-start gap-6 md:grid-cols-12">
        <Panel label="Events" className="md:col-span-6">
          <EmptyState>
            Each event in causal order, with its raw JSON one keystroke away.
            {found ? null : " No trial is in this build."}
          </EmptyState>
        </Panel>
        <Panel label="Actors" className="md:col-span-6 lg:col-span-2">
          <ActorLegend />
        </Panel>
        <Panel label="Fault marks" className="md:col-span-12 lg:col-span-4">
          <FaultLegend />
        </Panel>
      </div>
    </div>
  );
}
