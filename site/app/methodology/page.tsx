import type { Metadata } from "next";

import { DemoBanner } from "@/components/DemoBanner";
import { MethodologyTabs } from "@/components/methodology-tabs";
import { PageHeader, Panel, Readout } from "@/components/panel";
import { evidence, latestBatch } from "@/lib/batches";

export const metadata: Metadata = { title: "Methodology" };

export default function MethodologyPage() {
  const batch = latestBatch(evidence());
  const m = batch?.manifest;
  return (
    <div className="grid gap-10">
      <DemoBanner sources={batch ? [batch] : []} />
      <PageHeader
        experience="Methodology"
        route="/methodology"
        title="How we know we didn’t create the bug"
      >
        <p>
          A result is only as good as the harness behind it. This page will carry the controls that
          ran with each batch, the clauses every verdict cites, how the numbers are worded, and what
          is not claimed.
        </p>
      </PageHeader>
      {batch && m ? (
        <Panel label="Environment">
          <p className="mb-5 text-sm text-ink-muted">
            What the manifest of batch <span className="readout text-ink">{batch.id}</span> records.
          </p>
          <dl className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-6">
            <Readout label="Runner" value={m.runner} />
            <Readout label="System" value={`${m.os_system} ${m.os_release}`} />
            <Readout label="Python" value={m.python_version} />
            <Readout label="Postgres" value={m.postgres.server_version ?? null} />
            <Readout label="synchronous_commit" value={m.postgres.synchronous_commit ?? null} />
            <Readout label="Toxiproxy" value={m.toxiproxy_version} />
          </dl>
        </Panel>
      ) : null}
      <MethodologyTabs />
    </div>
  );
}
