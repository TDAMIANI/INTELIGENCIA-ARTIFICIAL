import { useEffect, useMemo, useRef, useState } from "react";

import { fmtDateTime, fmtNumber, fmtTime } from "../format";

export interface Point {
  t: number; // epoch ms
  v: number;
}

export interface Threshold {
  value: number;
  label: string;
  status: "warning" | "critical" | "serious";
}

interface Props {
  points: Point[];
  unit?: string;
  height?: number;
  color?: string;
  thresholds?: Threshold[];
  yMin?: number;
  yMax?: number;
  label: string; // nombre de la serie (para el tooltip y lectores de pantalla)
}

const PAD = { top: 12, right: 12, bottom: 22, left: 44 };

function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? raw;
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
  return ticks;
}

/** Gráfico de línea de una serie con cursor y tooltip. Un solo eje Y. */
export function LineChart({ points, unit = "", height = 180, color = "var(--series-1)", thresholds = [], yMin, yMax, label }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const geo = useMemo(() => {
    if (points.length === 0) return null;
    const ts = points.map((p) => p.t);
    const vs = points.map((p) => p.v);
    const t0 = Math.min(...ts);
    const t1 = Math.max(...ts);
    let lo = yMin ?? Math.min(...vs, ...thresholds.map((t) => t.value));
    let hi = yMax ?? Math.max(...vs, ...thresholds.map((t) => t.value));
    if (hi - lo < 1e-9) {
      lo -= 1;
      hi += 1;
    }
    const padV = (hi - lo) * 0.08;
    if (yMin === undefined) lo -= padV;
    if (yMax === undefined) hi += padV;
    const iw = width - PAD.left - PAD.right;
    const ih = height - PAD.top - PAD.bottom;
    const x = (t: number) => PAD.left + (t1 === t0 ? iw / 2 : ((t - t0) / (t1 - t0)) * iw);
    const y = (v: number) => PAD.top + ih - ((v - lo) / (hi - lo)) * ih;
    const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    const yTicks = niceTicks(lo, hi);
    const xTicks = [t0, t0 + (t1 - t0) / 2, t1];
    return { x, y, path, yTicks, xTicks, t0, t1, iw };
  }, [points, width, height, thresholds, yMin, yMax]);

  if (!geo) return <div className="empty">Sin datos en el período</div>;

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const px = e.clientX - rect.left;
    let best = 0;
    let bestD = Infinity;
    points.forEach((p, i) => {
      const d = Math.abs(geo.x(p.t) - px);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    setHover(best);
  };

  const hp = hover !== null ? points[hover] : null;
  const spanH = (geo.t1 - geo.t0) / 3_600_000;
  const tickFmt = (t: number) => (spanH > 36 ? fmtDateTime(new Date(t).toISOString()) : fmtTime(new Date(t).toISOString()));

  return (
    <div className="chart" ref={wrap}>
      <svg height={height} role="img" aria-label={`${label}: ${points.length} puntos`}>
        <g className="axis">
          {geo.yTicks.map((v) => (
            <g key={v}>
              <line x1={PAD.left} x2={width - PAD.right} y1={geo.y(v)} y2={geo.y(v)} stroke="var(--grid)" />
              <text x={PAD.left - 8} y={geo.y(v)} dy="0.32em" textAnchor="end">
                {fmtNumber(v, Math.abs(v) >= 100 ? 0 : 1)}
              </text>
            </g>
          ))}
          {geo.xTicks.map((t, i) => (
            <text key={t} x={geo.x(t)} y={height - 6} textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}>
              {tickFmt(t)}
            </text>
          ))}
        </g>
        {thresholds.map((th) => (
          <g key={th.label}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={geo.y(th.value)}
              y2={geo.y(th.value)}
              stroke={`var(--${th.status})`}
              strokeDasharray="4 4"
              strokeWidth={1.5}
            />
            <text x={width - PAD.right} y={geo.y(th.value) - 4} textAnchor="end" fontSize={10} fill="var(--text-secondary)">
              {th.label}
            </text>
          </g>
        ))}
        <path d={geo.path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {hp && (
          <g>
            <line x1={geo.x(hp.t)} x2={geo.x(hp.t)} y1={PAD.top} y2={height - PAD.bottom} stroke="var(--line-strong)" />
            <circle cx={geo.x(hp.t)} cy={geo.y(hp.v)} r={4.5} fill={color} stroke="var(--surface-1)" strokeWidth={2} />
          </g>
        )}
        <rect
          x={PAD.left}
          y={0}
          width={geo.iw}
          height={height}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      {hp && (
        <div className="tooltip" style={{ left: geo.x(hp.t), top: geo.y(hp.v) }}>
          <div className="muted">{fmtDateTime(new Date(hp.t).toISOString())}</div>
          <div>
            {label}: <strong className="num">{fmtNumber(hp.v)}</strong> {unit}
          </div>
        </div>
      )}
    </div>
  );
}
