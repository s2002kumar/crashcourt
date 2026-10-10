"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";

const NODES: Node[] = [];
const EDGES: Edge[] = [];

export default function LineageCanvas({ note }: { note: string }) {
  return (
    <div className="lineage relative h-80 overflow-hidden rounded-panel border border-rule bg-panel">
      <ReactFlow
        nodes={NODES}
        edges={EDGES}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
        aria-label="Checkpoint graph"
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="var(--rule-strong)" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
      <p className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-ink-muted">
        {note}
      </p>
    </div>
  );
}
