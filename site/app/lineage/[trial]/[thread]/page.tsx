import type { Metadata } from "next";

import { LineageView } from "@/components/lineage/lineage-view";
import { EmptyState, PageHeader, Panel } from "@/components/panel";
import { isEmptySegment, paramsOrEmpty } from "@/lib/routes";

type Params = { trial: string; thread: string };

export const dynamicParams = false;

export function generateStaticParams(): Params[] {
  return paramsOrEmpty(["trial", "thread"], []);
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { trial, thread } = await params;
  return isEmptySegment(trial, thread)
    ? { title: "Checkpoint lineage", robots: { index: false } }
    : { title: `Lineage of ${thread} in ${trial}` };
}

export default async function LineagePage({ params }: { params: Promise<Params> }) {
  const { trial, thread } = await params;
  const empty = isEmptySegment(trial, thread);
  return (
    <div className="grid gap-10">
      <PageHeader
        experience="Checkpoint lineage"
        route={empty ? "/lineage/[trial]/[thread]" : `/lineage/${trial}/${thread}`}
        title={empty ? "Where one thread’s history forked" : `Thread ${thread}`}
      >
        <p>
          Every checkpoint of one thread as a graph, read from the database snapshot: which worker
          each was acknowledged to, the head the next reader chose, and branches nobody will resume.
        </p>
      </PageHeader>
      <Panel label="Checkpoint graph">
        <LineageView />
      </Panel>
      <Panel label="Anomalies">
        <EmptyState>
          Forks, orphaned effects, and head changes the checker found. None in this build.
        </EmptyState>
      </Panel>
    </div>
  );
}
