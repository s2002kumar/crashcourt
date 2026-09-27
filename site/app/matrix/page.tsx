import type { Metadata } from "next";

import { EmptyState, PageHeader, Panel } from "@/components/panel";
import { VerdictLegend } from "@/components/verdict";

export const metadata: Metadata = { title: "Fault matrix" };

export default function MatrixPage() {
  return (
    <div className="grid gap-10">
      <PageHeader
        experience="Fault matrix"
        route="/matrix"
        title="Every fault against every target"
      >
        <p>
          Rows are workload and fault; columns are target and durability mode. Each cell shows its
          verdict, how many trials showed the anomaly out of how many ran, and opens those trials.
        </p>
      </PageHeader>
      <div className="grid gap-6 lg:grid-cols-12">
        <Panel label="Matrix" className="lg:col-span-8">
          <EmptyState>
            No validated batch is in this build. The matrix is a plain table with sticky headers and
            arrow-key navigation, so it reads the same to a screen reader and on paper.
          </EmptyState>
        </Panel>
        <Panel label="Verdicts" className="lg:col-span-4">
          <VerdictLegend />
        </Panel>
      </div>
    </div>
  );
}
