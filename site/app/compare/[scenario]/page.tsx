import type { Metadata } from "next";
import Link from "next/link";

import { DemoBanner } from "@/components/DemoBanner";
import { OutcomeList } from "@/components/outcomes";
import { EmptyState, PageHeader, Panel, Readout } from "@/components/panel";
import { comparisons, evidence, runtimeOf } from "@/lib/batches";
import { compareParams, isEmptySegment, pagePath, paramsOrEmpty } from "@/lib/routes";

type Params = { scenario: string };

export const dynamicParams = false;

// Hidden until at least two targets have validated runs: never linked, never indexed, and in a release
// build only this placeholder is exported (lib/batches.ts, `comparisons`).
export function generateStaticParams(): Params[] {
  return paramsOrEmpty(["scenario"], compareParams(evidence()));
}

export const metadata: Metadata = { title: "Compare systems", robots: { index: false } };

export default async function ComparePage({ params }: { params: Promise<Params> }) {
  const { scenario } = await params;
  const found = isEmptySegment(scenario)
    ? undefined
    : comparisons(evidence()).find((c) => c.scenario === scenario);
  return (
    <div className="grid gap-10">
      <DemoBanner sources={found ? [found] : []} />
      <PageHeader
        experience="Compare systems"
        route={found ? `/compare/${scenario}` : "/compare/[scenario]"}
        title="One scenario, several runtimes"
      >
        <p>
          Each runtime&rsquo;s documented semantics beside its measured behavior for the same fault.
        </p>
      </PageHeader>
      {found ? (
        <>
          <Panel label="Scenario">
            <dl>
              <Readout label="Content hash" value={found.scenario} />
            </dl>
          </Panel>
          <div className="grid items-start gap-6 lg:grid-cols-2">
            {found.batches.map((b) => (
              <Panel key={b.id} label="Runtime">
                <p className="readout text-sm text-ink">
                  {runtimeOf(b)
                    .map((name) => `${name} ${b.manifest.packages[name]}`)
                    .join(", ")}
                </p>
                <p className="mt-1 text-sm text-ink-muted">
                  Batch <span className="readout text-ink">{b.id}</span>
                </p>
                <ul className="mt-5 grid gap-6">
                  {b.trials.map((t) => (
                    <li key={t.dir} className="grid gap-3">
                      <Link
                        href={pagePath("trials", t.cell, String(t.trial))}
                        className="readout text-sm text-ink underline"
                      >
                        {`${t.cell} #${t.trial}`}
                      </Link>
                      <OutcomeList results={t.result.results} />
                    </li>
                  ))}
                </ul>
              </Panel>
            ))}
          </div>
        </>
      ) : (
        <Panel label="Status">
          <EmptyState>
            Hidden until at least two targets have validated runs. Until then there is nothing to
            compare.
          </EmptyState>
        </Panel>
      )}
    </div>
  );
}
