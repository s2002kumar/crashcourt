"use client";

// The failure explorer's frame: one lane per actor over a shared time axis, zoomable and pannable with
// d3-zoom. Events are drawn by later work; this frame fixes the lanes, the scale, and the controls.

import { scaleLinear } from "d3-scale";
import { select } from "d3-selection";
import { zoom as d3zoom, zoomIdentity, type D3ZoomEvent, type ZoomBehavior } from "d3-zoom";
import { useEffect, useMemo, useRef, useState } from "react";

import { ACTORS, actorToken } from "@/lib/tokens";

const LABEL_W = 116;
const LANE_H = 44;
const AXIS_H = 28;
const HEIGHT = AXIS_H + LANE_H * ACTORS.length;
const MAX_ZOOM = 64;

export interface LanesFrameProps {
  /** Time span of the trial in milliseconds, from the merged history; null when there are no events. */
  readonly spanMs: readonly [number, number] | null;
}

// Wheel zooms only with Ctrl or Cmd, so scrolling the page over the timeline still scrolls the page.
function zoomFilter(e: Event): boolean {
  if (e.type === "wheel") return (e as WheelEvent).ctrlKey || (e as WheelEvent).metaKey;
  return !(e as MouseEvent).ctrlKey && !(e as MouseEvent).button;
}

export function LanesFrame({ spanMs }: LanesFrameProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const behavior = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const [width, setWidth] = useState(960);
  const [k, setK] = useState(1);
  const [x, setX] = useState(0);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(320, Math.round(entry.contentRect.width)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const plotW = width - LABEL_W;

  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const extent: [[number, number], [number, number]] = [
      [0, 0],
      [plotW, HEIGHT],
    ];
    const z = d3zoom<SVGSVGElement, unknown>()
      .filter(zoomFilter)
      .scaleExtent([1, MAX_ZOOM])
      .extent(extent)
      .translateExtent(extent)
      .on("zoom", (e: D3ZoomEvent<SVGSVGElement, unknown>) => {
        setK(e.transform.k);
        setX(e.transform.x);
      });
    behavior.current = z;
    select(el).call(z);
    return () => {
      select(el).on(".zoom", null);
    };
  }, [plotW]);

  const scale = useMemo(() => {
    const base = scaleLinear()
      .domain(spanMs ?? [0, 1])
      .range([0, plotW]);
    return zoomIdentity.translate(x, 0).scale(k).rescaleX(base);
  }, [spanMs, plotW, k, x]);

  const ticks = scale.ticks(Math.max(2, Math.floor(plotW / 120)));
  const format = scale.tickFormat(undefined, "~s");

  const zoomBy = (factor: number) => {
    const el = svg.current;
    if (el && behavior.current) select(el).call(behavior.current.scaleBy, factor);
  };
  const reset = () => {
    const el = svg.current;
    if (el && behavior.current) select(el).call(behavior.current.transform, zoomIdentity);
  };

  const lanes = ACTORS.map((a) => actorToken(a).label).join(", ");
  const button =
    "label rounded-panel border border-rule px-2.5 py-1 text-ink-muted hover:text-ink disabled:opacity-40";

  return (
    <figure className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <figcaption className="text-sm text-ink-muted">
          {spanMs ? "Time since the trial started, in milliseconds." : "No events in this build."}
        </figcaption>
        <div className="flex gap-1.5" role="group" aria-label="Timeline zoom">
          <button
            type="button"
            className={button}
            onClick={() => zoomBy(2)}
            disabled={k >= MAX_ZOOM}
          >
            Zoom in
          </button>
          <button type="button" className={button} onClick={() => zoomBy(0.5)} disabled={k <= 1}>
            Zoom out
          </button>
          <button type="button" className={button} onClick={reset} disabled={k === 1 && x === 0}>
            Reset
          </button>
        </div>
      </div>
      <div ref={wrap} className="overflow-hidden rounded-panel border border-rule bg-panel">
        <svg
          ref={svg}
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={`Timeline with lanes for ${lanes}. ${spanMs ? "" : "No events."}`}
          className="block cursor-grab touch-none active:cursor-grabbing"
        >
          <g transform={`translate(${LABEL_W},0)`}>
            {ticks.map((t) => (
              <g key={t} transform={`translate(${scale(t)},0)`}>
                <line y1={AXIS_H - 6} y2={HEIGHT} stroke="var(--rule)" />
                {spanMs ? (
                  <text
                    y={AXIS_H - 11}
                    textAnchor="middle"
                    fontSize={10}
                    fontFamily="var(--font-mono)"
                    fill="var(--ink-faint)"
                  >
                    {format(t)}
                  </text>
                ) : null}
              </g>
            ))}
          </g>
          {ACTORS.map((a, i) => {
            const token = actorToken(a);
            return (
              <g key={a} transform={`translate(0,${AXIS_H + i * LANE_H})`}>
                <line x1={0} x2={width} stroke="var(--rule)" />
                <rect x={14} y={LANE_H / 2 - 5} width={6} height={10} rx={1} fill={token.color} />
                <text x={28} y={LANE_H / 2 + 4} fontSize={12} fill="var(--ink)">
                  {token.label}
                </text>
                <line
                  x1={LABEL_W}
                  x2={width}
                  y1={LANE_H / 2}
                  y2={LANE_H / 2}
                  stroke={token.color}
                  strokeOpacity={0.55}
                  strokeDasharray="1 5"
                />
              </g>
            );
          })}
        </svg>
      </div>
    </figure>
  );
}
