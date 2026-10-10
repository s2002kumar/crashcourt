import type { Metadata } from "next";
import Link from "next/link";

import { DemoBanner } from "@/components/DemoBanner";
import { EmptyState, PageHeader, Panel } from "@/components/panel";
import { OtherOutcomeLegend, VerdictLegend } from "@/components/verdict";
import { evidence, latestBatch } from "@/lib/batches";
import { pagePath } from "@/lib/routes";

export const metadata: Metadata = { title: "Fault matrix" };

export default function MatrixPage() {
  const batch = latestBatch(evidence());
  const cells = new Map<string, number[]>();
  for (const t of batch?.trials ?? []) cells.set(t.cell, [...(cells.get(t.cell) ?? []), t.trial]);
  return (
    <div className="grid gap-10">
      <DemoBanner sources={batch ? [batch] : []} />
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
          {batch ? (
            <table className="w-full text-left text-sm">
              <caption className="pb-3 text-left text-ink-muted">
                The cells of batch <span className="readout text-ink">{batch.id}</span> and the
                trials each ran. Counts and intervals come from the batch summary, which this build
                does not have.
              </caption>
              <thead>
                <tr className="border-b border-rule">
                  <th scope="col" className="label py-2 pr-4 font-normal">
                    Cell
                  </th>
                  <th scope="col" className="label py-2 font-normal">
                    Trials
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...cells].map(([cell, trials]) => (
                  <tr key={cell} className="border-b border-rule last:border-0">
                    <th scope="row" className="readout py-2.5 pr-4 font-normal text-ink">
                      {cell}
                    </th>
                    <td className="py-2.5">
                      <ul className="flex flex-wrap gap-x-3 gap-y-1">
                        {trials.map((trial) => (
                          <li key={trial}>
                            <Link
                              href={pagePath("trials", cell, String(trial))}
                              aria-label={`Trial ${trial} of ${cell}`}
                              className="readout text-ink"
                            >
                              #{trial}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState>
              No validated batch is in this build. The matrix is a plain table with sticky headers
              and arrow-key navigation, so it reads the same to a screen reader and on paper.
            </EmptyState>
          )}
        </Panel>
        <div className="grid content-start gap-6 lg:col-span-4">
          <Panel label="Verdicts">
            <VerdictLegend />
          </Panel>
          <Panel label="Outcomes that are not verdicts">
            <OtherOutcomeLegend />
          </Panel>
        </div>
      </div>
    </div>
  );
}
