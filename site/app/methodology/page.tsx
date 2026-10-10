import type { Metadata } from "next";

import { MethodologyTabs } from "@/components/methodology-tabs";
import { PageHeader } from "@/components/panel";

export const metadata: Metadata = { title: "Methodology" };

export default function MethodologyPage() {
  return (
    <div className="grid gap-10">
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
      <MethodologyTabs />
    </div>
  );
}
