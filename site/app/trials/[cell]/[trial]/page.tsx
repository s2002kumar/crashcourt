import type { Metadata } from "next";

import { ActorLegend, FaultLegend } from "@/components/legends";
import { EmptyState, PageHeader, Panel } from "@/components/panel";
import { LanesFrame } from "@/components/timeline/lanes-frame";
import { isEmptySegment, paramsOrEmpty } from "@/lib/routes";

type Params = { cell: string; trial: string };

export const dynamicParams = false;

export function generateStaticParams(): Params[] {
  return paramsOrEmpty(["cell", "trial"], []);
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { cell, trial } = await params;
  return isEmptySegment(cell, trial)
    ? { title: "Failure explorer", robots: { index: false } }
    : { title: `Trial ${cell} #${trial}` };
}

export default async function TrialPage({ params }: { params: Promise<Params> }) {
  const { cell, trial } = await params;
  const empty = isEmptySegment(cell, trial);
  return (
    <div className="grid gap-10">
      <PageHeader
        experience="Failure explorer"
        route={empty ? "/trials/[cell]/[trial]" : `/trials/${cell}/${trial}`}
        title={empty ? "One trial, step by step" : `Trial ${cell} #${trial}`}
      >
        <p>
          Every process on its own lane, faults where they landed, and causal arrows between them,
          played back in the order the checker proved, not the order the clocks say.
        </p>
      </PageHeader>
      <Panel label="Timeline">
        <LanesFrame spanMs={null} />
      </Panel>
      <div className="grid items-start gap-6 md:grid-cols-12">
        <Panel label="Events" className="md:col-span-6">
          <EmptyState>
            Each event in causal order, with its raw JSON one keystroke away. No trial is in this
            build.
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
