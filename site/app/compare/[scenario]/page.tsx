import type { Metadata } from "next";

import { EmptyState, PageHeader, Panel } from "@/components/panel";
import { paramsOrEmpty } from "@/lib/routes";

type Params = { scenario: string };

export const dynamicParams = false;

// Hidden until at least two targets have validated runs: not linked, not indexed, and only this
// placeholder is exported.
export function generateStaticParams(): Params[] {
  return paramsOrEmpty(["scenario"], []);
}

export const metadata: Metadata = { title: "Compare systems", robots: { index: false } };

export default function ComparePage() {
  return (
    <div className="grid gap-10">
      <PageHeader
        experience="Compare systems"
        route="/compare/[scenario]"
        title="One scenario, several runtimes"
      >
        <p>
          Each runtime&rsquo;s documented semantics beside its measured behavior for the same fault.
        </p>
      </PageHeader>
      <Panel label="Status">
        <EmptyState>
          Hidden until at least two targets have validated runs. Until then there is nothing to
          compare.
        </EmptyState>
      </Panel>
    </div>
  );
}
