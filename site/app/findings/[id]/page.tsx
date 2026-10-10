import type { Metadata } from "next";
import Link from "next/link";

import { DemoBanner } from "@/components/DemoBanner";
import { OutcomeList } from "@/components/outcomes";
import { PageHeader, Panel, Readout } from "@/components/panel";
import { evidence, findings } from "@/lib/batches";
import { findingParams, isEmptySegment, pagePath, paramsOrEmpty } from "@/lib/routes";

type Params = { id: string };

export const dynamicParams = false;

export function generateStaticParams(): Params[] {
  return paramsOrEmpty(["id"], findingParams(evidence()));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { id } = await params;
  return isEmptySegment(id) ? { title: "Finding", robots: { index: false } } : { title: id };
}

const SECTIONS: readonly (readonly [label: string, holds: string])[] = [
  ["What happened", "Two plain sentences."],
  ["Why it matters", "One sentence for an engineer who self-hosts."],
  ["Contract", "The clause, its polarity, its source, and when it was retrieved."],
  ["Not claimed", "What this finding does not show."],
  ["Schedule", "The minimized schedule, step by step."],
  ["Repro", "The standalone command and files, and how often it reproduces."],
  ["Upstream", "The issue, its status, and any maintainer response, dated."],
];

export default async function FindingPage({ params }: { params: Promise<Params> }) {
  const { id } = await params;
  const finding = isEmptySegment(id) ? undefined : findings(evidence()).find((f) => f.id === id);
  return (
    <div className="grid gap-10">
      <DemoBanner sources={finding ? [finding] : []} />
      <PageHeader
        experience="Finding"
        route={finding ? `/findings/${id}` : "/findings/[id]"}
        title={finding ? id : "No finding has been published"}
      >
        <p>
          A finding page says what happened in plain language and carries its own evidence: the
          contract it is judged against, the verdict, the minimal schedule, and a repro anyone can
          run.
        </p>
      </PageHeader>
      <dl className="grid gap-px overflow-hidden rounded-panel border border-rule bg-rule">
        {SECTIONS.map(([label, holds]) => (
          <div key={label} className="grid gap-2 bg-panel p-5 md:grid-cols-[12rem_1fr] md:gap-6">
            <dt className="label pt-0.5">{label}</dt>
            <dd className="text-sm text-ink-muted">{holds}</dd>
          </div>
        ))}
      </dl>
      {finding ? (
        <>
          <Panel label="Repro">
            <dl>
              <Readout label="Reference" value={finding.repro} />
            </dl>
          </Panel>
          {finding.occurrences.map(({ trial, result }) => (
            <Panel key={`${trial.dir}:${result.invariant}`} label="Seen in">
              <p className="mb-4 text-sm">
                <Link
                  href={pagePath("trials", trial.cell, String(trial.trial))}
                  className="readout text-ink underline"
                >
                  {`${trial.cell} #${trial.trial}`}
                </Link>{" "}
                <span className="text-ink-muted">in batch</span>{" "}
                <span className="readout text-ink">{trial.batch}</span>
              </p>
              <OutcomeList results={[result]} />
            </Panel>
          ))}
        </>
      ) : (
        <Panel label="Evidence">
          <p className="text-sm text-ink-muted">
            Links to the failure explorer, the lineage, and the raw artifacts appear with a finding.
          </p>
        </Panel>
      )}
    </div>
  );
}
