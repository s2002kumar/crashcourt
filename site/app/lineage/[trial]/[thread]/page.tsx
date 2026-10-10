import type { Metadata } from "next";
import Link from "next/link";

import { DemoBanner } from "@/components/DemoBanner";
import { LineageView } from "@/components/lineage/lineage-view";
import { EmptyState, PageHeader, Panel, Readout } from "@/components/panel";
import { batchOf, evidence, findTrialByKey } from "@/lib/batches";
import { isEmptySegment, lineageParams, pagePath, paramsOrEmpty } from "@/lib/routes";

type Params = { trial: string; thread: string };

export const dynamicParams = false;

export function generateStaticParams(): Params[] {
  return paramsOrEmpty(["trial", "thread"], lineageParams(evidence()));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { trial, thread } = await params;
  return isEmptySegment(trial, thread)
    ? { title: "Checkpoint lineage", robots: { index: false } }
    : { title: `Lineage of ${thread} in ${trial}` };
}

const HEAD = "label py-2 pr-4 font-normal";
const CELL = "readout py-2 pr-4 break-all";

export default async function LineagePage({ params }: { params: Promise<Params> }) {
  const { trial, thread } = await params;
  const all = evidence();
  const found = isEmptySegment(trial, thread) ? undefined : findTrialByKey(all, trial);
  const batch = found ? batchOf(all, found) : undefined;
  const events = (found?.merged?.events ?? []).filter((e) => e.thread_id === thread);
  // As the histories record them: the checkpoints written and the effects produced, in causal order.
  const writes = events.filter(
    (e) => e.kind === "saver.put.return" || e.kind === "saver.put.raise",
  );
  const effects = events.filter((e) => e.kind === "effect.request");
  return (
    <div className="grid gap-10">
      <DemoBanner sources={found && batch ? [found, batch] : []} />
      <PageHeader
        experience="Checkpoint lineage"
        route={found ? `/lineage/${trial}/${thread}` : "/lineage/[trial]/[thread]"}
        title={found ? `Thread ${thread}` : "Where one thread’s history forked"}
      >
        <p>
          Every checkpoint of one thread as a graph, read from the database snapshot: which worker
          each was acknowledged to, the head the next reader chose, and branches nobody will resume.
        </p>
      </PageHeader>
      <Panel label="Checkpoint graph">
        <LineageView
          note={
            found
              ? "No database snapshot in this build. The checkpoint writes the histories record are listed below."
              : "No checkpoints in this build."
          }
        />
      </Panel>
      {found && batch ? (
        <>
          <Panel label="Trial">
            <dl className="grid grid-cols-2 gap-5 sm:grid-cols-4">
              <Readout label="Batch" value={batch.id} />
              <Readout label="Cell" value={found.cell} />
              <Readout label="Trial" value={String(found.trial)} />
              <Readout label="Thread" value={thread} />
            </dl>
            <p className="mt-5 text-sm">
              <Link
                href={pagePath("trials", found.cell, String(found.trial))}
                className="text-ink underline"
              >
                Open this trial in the failure explorer
              </Link>
            </p>
          </Panel>
          <div className="grid items-start gap-6 lg:grid-cols-12">
            <Panel label="Checkpoint writes" className="overflow-x-auto lg:col-span-7">
              <table className="w-full text-left text-sm">
                <caption className="pb-3 text-left text-ink-muted">
                  Each put the histories record for this thread, in causal order.
                </caption>
                <thead>
                  <tr className="border-b border-rule">
                    <th scope="col" className={HEAD}>
                      Checkpoint
                    </th>
                    <th scope="col" className={HEAD}>
                      Parent
                    </th>
                    <th scope="col" className={HEAD}>
                      Process
                    </th>
                    <th scope="col" className={HEAD}>
                      Event
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {writes.map((e) => (
                    <tr key={`${e.proc}:${e.seq}`} className="border-b border-rule last:border-0">
                      <td className={CELL}>{e.checkpoint_id}</td>
                      <td className={CELL}>{e.parent_checkpoint_id ?? "—"}</td>
                      <td className={CELL}>{e.proc}</td>
                      <td className={CELL}>{e.kind}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
            <Panel label="Effects" className="overflow-x-auto lg:col-span-5">
              {effects.length > 0 ? (
                <table className="w-full text-left text-sm">
                  <caption className="pb-3 text-left text-ink-muted">
                    Each effect requested on this thread and the checkpoint its worker had loaded.
                  </caption>
                  <thead>
                    <tr className="border-b border-rule">
                      <th scope="col" className={HEAD}>
                        Effect
                      </th>
                      <th scope="col" className={HEAD}>
                        Checkpoint
                      </th>
                      <th scope="col" className={HEAD}>
                        Process
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {effects.map((e) => (
                      <tr key={`${e.proc}:${e.seq}`} className="border-b border-rule last:border-0">
                        <td className={CELL}>{e.effect_key}</td>
                        <td className={CELL}>{e.checkpoint_id}</td>
                        <td className={CELL}>{e.proc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState>No effect was requested on this thread.</EmptyState>
              )}
            </Panel>
          </div>
        </>
      ) : null}
      <Panel label="Anomalies">
        <EmptyState>
          Forks, orphaned effects, and head changes the checker found.
          {found
            ? " The checker's lineage output is not in this build; the trial's outcomes are on its failure explorer page."
            : " None in this build."}
        </EmptyState>
      </Panel>
    </div>
  );
}
