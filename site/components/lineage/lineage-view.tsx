"use client";

// React Flow loads only on this route, after the page itself: it is the largest dependency in the site,
// and no other page should pay for it (see BUDGETS.md).

import dynamic from "next/dynamic";

const LineageCanvas = dynamic(() => import("./lineage-canvas"), {
  ssr: false,
  loading: () => (
    <div className="grid h-80 place-items-center rounded-panel border border-rule bg-panel">
      <p className="label">Loading graph</p>
    </div>
  ),
});

export function LineageView() {
  return <LineageCanvas />;
}
