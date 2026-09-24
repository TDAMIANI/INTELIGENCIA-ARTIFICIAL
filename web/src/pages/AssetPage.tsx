import { useMemo, useState } from "react";

import { api, type AssetNode, type IndicatorResult } from "../api";
import { flatten } from "../components/AssetTree";
import { Ago, ErrorBox, EvidenceThumb, eventLabel, Loading, Panel } from "../components/Common";
import { ConveyorSchematic, StatusLegend } from "../components/ConveyorSchematic";
import { LineChart, type Threshold } from "../components/LineChart";
import { HealthBadge, StatusIcon, StatusTag } from "../components/Status";
import {
  fmtDateTime,
  fmtEta,
  fmtNumber,
  healthStatus,
  INDICATOR_LABEL,
  metricLabel,
  PRIORITY_LABEL,
  PRIORITY_STATUS,
} from "../format";
import { href, useResource } from "../hooks";
import { useLiveVersion } from "../live";

const RANGES = [
  { label: "1 h", hours: 1, bucket: undefined },
  { label: "6 h", hours: 6, bucket: 60 },
  { label: "24 h", hours: 24, bucket: 300 },
] as const;

// Umbrales de referencia para leer los gráficos (mismos que config/plant.yaml).
const THRESHOLDS: Record<string, Threshold[]> = {
  vibration_rms_mm_s: [
    { value: 4.5, label: "ISO 10816 zona C", status: "warning" },
    { value: 7.1, label: "zona D", status: "critical" },
  ],
  belt_edge_offset_mm: [
    { value: 30, label: "advertencia", status: "warning" },
    { value: 50, label: "crítico", status: "critical" },
  ],
};

export function AssetPage({ code }: { code: string }) {
  const twinV = useLiveVersion("twin");
  const alarmsV = useLiveVersion("alarms");
  const eventsV = useLiveVersion("events");

  const detail = useResource(() => api.asset(code), [code]);
  const tree = useResource(api.tree, [twinV]);
  const health = useResource(() => api.health(code), [code, twinV]);
  const recs = useResource(() => api.recommendations({ asset: code }), [code, twinV]);
  const alarms = useResource(() => api.alarms({ asset: code, limit: 20 }), [code, alarmsV]);
  const events = useResource(() => api.events({ asset: code, limit: 12 }), [code, eventsV]);

  const node = useMemo(() => (tree.data ? flatten(tree.data).find((n) => n.code === code) : undefined), [tree.data, code]);
  const descendants = useMemo(() => (node ? flatten(node.children) : []), [node]);
  const hasIdlers = descendants.some((d) => /-IDL-\d+$/.test(d.code));

  const [selected, setSelected] = useState<string | undefined>();
  const focus = selected ?? code;

  if (detail.error) return <ErrorBox error={detail.error} />;
  if (!detail.data) return <Loading />;
  const d = detail.data;
  const status = healthStatus(health.data?.health_index ?? d.health_index);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">
            {d.parent_code ? <a href={href("activo", d.parent_code)}>← {d.parent_code}</a> : <a href={href("")}>← Planta</a>}
            {"  ·  "}
            {d.code} · {levelLabel(d.level)}
          </div>
          <h1>{d.name}</h1>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="kpi-label">Índice de salud</div>
          <div className="kpi-value" style={{ fontSize: 48 }}>
            {health.data?.health_index === null || health.data?.health_index === undefined ? "—" : Math.round(health.data.health_index)}
            <small>/100</small>
          </div>
          <StatusTag status={status} />
          {health.data?.updated_at && <div className="muted" style={{ fontSize: 12 }}>actualizado <Ago iso={health.data.updated_at} /></div>}
        </div>
      </div>

      {hasIdlers && node && (
        <div style={{ marginBottom: 16 }}>
          <TwinPanel node={node} descendants={descendants} selected={selected} onSelect={(c) => setSelected(c === selected ? undefined : c)} />
        </div>
      )}

      <div className="grid cols-main">
        <div className="grid" style={{ alignContent: "start" }}>
          {selected && selected !== code && <SelectedComponent code={selected} onClose={() => setSelected(undefined)} />}
          <Panel title="Por qué tiene esta salud">
            <HealthExplanation indicators={health.data?.indicators ?? []} worstChild={health.data?.worst_child ?? null} />
          </Panel>
          <TrendsPanel code={focus} />
          <Panel title="Evolución de la salud · 24 h">
            <HealthHistory code={code} version={twinV} />
          </Panel>
        </div>

        <div className="grid" style={{ alignContent: "start" }}>
          <Panel title="Mantenimiento recomendado" flush>
            {recs.data?.length ? (
              <ul className="list">
                {recs.data.map((r) => (
                  <li key={r.id}>
                    <StatusIcon status={PRIORITY_STATUS[r.priority]} />
                    <div style={{ minWidth: 0 }}>
                      <div className="item-title">{r.action}</div>
                      <div className="secondary" style={{ fontSize: 13 }}>{r.reason}</div>
                      <div className="item-meta">
                        <span>{PRIORITY_LABEL[r.priority]}</span>
                        <span>vence <Ago iso={r.due_by} /></span>
                        {!r.condition_active && <span>condición normalizada</span>}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="empty">Sin recomendaciones pendientes</div>
            )}
          </Panel>

          <Panel title="Alarmas" flush>
            {alarms.data?.length ? (
              <ul className="list">
                {alarms.data.slice(0, 8).map((a) => (
                  <li key={a.id}>
                    <StatusIcon status={a.status === "cleared" ? "good" : a.severity === "critical" ? "critical" : "warning"} />
                    <div style={{ minWidth: 0 }}>
                      <div className="item-title">{a.message}</div>
                      <div className="item-meta">
                        <span>{a.status === "active" ? "activa" : "normalizada"}</span>
                        <Ago iso={a.raised_at} />
                        <span>{a.asset_code}</span>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="empty">Sin alarmas</div>
            )}
          </Panel>

          <Panel title="Evidencias de visión" flush>
            {events.data?.length ? (
              <ul className="list">
                {events.data.slice(0, 4).map((e) => (
                  <li key={e.id}>
                    <EvidenceThumb event={e} size={110} />
                    <div style={{ minWidth: 0 }}>
                      <div className="item-title">{eventLabel(e.type)}</div>
                      <div className="secondary" style={{ fontSize: 13 }}>{e.message}</div>
                      <div className="item-meta">
                        <Ago iso={e.ts} />
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="empty">Sin detecciones</div>
            )}
          </Panel>

          {(health.data?.children.length ?? 0) > 0 && !hasIdlers && (
            <Panel title="Componentes" flush>
              <table>
                <tbody>
                  {health.data!.children.map((c) => (
                    <tr key={c.code}>
                      <td>
                        <a href={href("activo", c.code)}>{c.name}</a> <span className="muted num" style={{ fontSize: 12 }}>{c.code}</span>
                      </td>
                      <td className="num">
                        <HealthBadge value={c.health_index} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}

          <Panel title="Ficha técnica">
            <AssetInfo detail={d} />
          </Panel>
        </div>
      </div>
    </>
  );
}

function levelLabel(level: string): string {
  return { plant: "Planta", area: "Área", system: "Sistema", subunit: "Subunidad", component: "Componente" }[level] ?? level;
}

function TwinPanel({
  node,
  descendants,
  selected,
  onSelect,
}: {
  node: AssetNode;
  descendants: AssetNode[];
  selected?: string;
  onSelect: (code: string) => void;
}) {
  const twinV = useLiveVersion("twin");
  const idlerGroup = descendants.find((d) => d.children.some((c) => /-IDL-\d+$/.test(c.code)));
  const latest = useResource(() => api.latest(idlerGroup?.code ?? node.code, true), [idlerGroup?.code, twinV], 15000);
  const temps = useMemo(() => {
    const out: Record<string, number> = {};
    latest.data?.forEach((v) => {
      if (v.metric === "max_temp_c") out[v.asset_code] = v.value;
    });
    return out;
  }, [latest.data]);
  return (
    <Panel title={`Gemelo digital · ${node.code}`} actions={<StatusLegend />}>
      <ConveyorSchematic code={node.code} components={descendants} temps={temps} selected={selected} onSelect={onSelect} />
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        Tocá un polín, el motor o la banda para ver su detalle. Los números sobre los polines comprometidos son su temperatura máxima (°C).
      </div>
    </Panel>
  );
}

function SelectedComponent({ code, onClose }: { code: string; onClose: () => void }) {
  const twinV = useLiveVersion("twin");
  const health = useResource(() => api.health(code), [code, twinV]);
  const h = health.data;
  return (
    <Panel
      title={h ? `${h.asset_name}` : code}
      actions={
        <div className="btn-row">
          <a className="btn ghost" href={href("activo", code)}>Abrir ficha</a>
          <button className="btn ghost" onClick={onClose} aria-label="Cerrar detalle">✕</button>
        </div>
      }
    >
      {h ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
            <HealthBadge value={h.health_index} />
            <StatusTag status={healthStatus(h.health_index)} />
            <span className="muted num" style={{ fontSize: 12 }}>{h.asset_code}</span>
          </div>
          <HealthExplanation indicators={h.indicators} worstChild={h.worst_child} />
        </div>
      ) : (
        <Loading />
      )}
    </Panel>
  );
}

function HealthExplanation({ indicators, worstChild }: { indicators: IndicatorResult[]; worstChild: string | null }) {
  if (indicators.length === 0) {
    return worstChild ? (
      <p className="secondary" style={{ margin: 0 }}>
        La salud de este activo es la de su componente más comprometido: <a href={href("activo", worstChild)}>{worstChild}</a>.
      </p>
    ) : (
      <p className="muted" style={{ margin: 0 }}>Este activo no tiene indicadores con datos (no hay sensores que lo midan).</p>
    );
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Indicador</th>
          <th className="num">Valor</th>
          <th>Severidad</th>
          <th className="num">Tendencia</th>
          <th className="num">A zona crítica</th>
        </tr>
      </thead>
      <tbody>
        {indicators.map((i) => {
          const sevStatus = i.severity >= 80 ? "critical" : i.severity >= 40 ? "serious" : i.severity >= 20 ? "warning" : "good";
          return (
            <tr key={i.indicator}>
              <td>
                {INDICATOR_LABEL[i.indicator] ?? i.indicator}
                {i.stale && <div className="muted" style={{ fontSize: 12 }}>último valor (correa detenida)</div>}
              </td>
              <td className="num">{fmtNumber(i.value)}</td>
              <td style={{ minWidth: 140 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="meter" style={{ flex: 1 }}>
                    <span style={{ width: `${i.severity}%`, background: `var(--${sevStatus})` }} />
                  </span>
                  <span className="num" style={{ fontSize: 12 }}>{Math.round(i.severity)}</span>
                </div>
              </td>
              <td className="num">{i.trend_per_h === null ? "—" : `${i.trend_per_h > 0 ? "+" : ""}${fmtNumber(i.trend_per_h)}/h`}</td>
              <td className="num">{i.eta_critical_h === null ? "—" : `~${fmtEta(i.eta_critical_h)}`}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function TrendsPanel({ code }: { code: string }) {
  const latest = useResource(() => api.latest(code), [code], 15000);
  const metrics = useMemo(
    () => (latest.data ?? []).map((v) => v.metric).filter((m) => !["running", "belt_detected"].includes(m)),
    [latest.data],
  );
  const [metric, setMetric] = useState<string>();
  const [range, setRange] = useState<(typeof RANGES)[number]>(RANGES[0]);
  const active = metric && metrics.includes(metric) ? metric : metrics[0];
  const start = new Date(Date.now() - range.hours * 3600_000).toISOString();
  const series = useResource(
    () => (active ? api.series(code, active, start, range.bucket) : Promise.resolve(undefined)),
    [code, active, range.hours],
    15000,
  );
  const points = useMemo(() => (series.data?.points ?? []).map((p) => ({ t: new Date(p.ts).getTime(), v: p.value })), [series.data]);
  const info = active ? metricLabel(active) : { label: "", unit: "" };

  return (
    <Panel
      title={`Tendencias · ${code}`}
      actions={
        <div className="segmented" role="group" aria-label="Período">
          {RANGES.map((r) => (
            <button key={r.label} aria-pressed={r === range} onClick={() => setRange(r)}>
              {r.label}
            </button>
          ))}
        </div>
      }
    >
      {metrics.length === 0 ? (
        <div className="empty">Este activo no tiene mediciones recientes.</div>
      ) : (
        <>
          <div className="segmented" role="group" aria-label="Medición" style={{ marginBottom: 12, flexWrap: "wrap" }}>
            {metrics.map((m) => (
              <button key={m} aria-pressed={m === active} onClick={() => setMetric(m)}>
                {metricLabel(m).label}
              </button>
            ))}
          </div>
          <LineChart points={points} unit={info.unit} label={info.label} thresholds={active ? THRESHOLDS[active] : []} />
          {range.bucket && <div className="muted" style={{ fontSize: 12 }}>Promedios cada {range.bucket / 60} min</div>}
        </>
      )}
    </Panel>
  );
}

function HealthHistory({ code, version }: { code: string; version: number }) {
  const start = new Date(Date.now() - 24 * 3600_000).toISOString();
  const hist = useResource(() => api.healthHistory(code, start), [code, version]);
  const points = useMemo(() => (hist.data ?? []).map((p) => ({ t: new Date(p.ts).getTime(), v: p.health_index })), [hist.data]);
  return (
    <LineChart
      points={points}
      label="Índice de salud"
      yMin={0}
      yMax={100}
      height={150}
      thresholds={[
        { value: 60, label: "atención", status: "warning" },
        { value: 40, label: "crítico", status: "critical" },
      ]}
    />
  );
}

function AssetInfo({ detail }: { detail: import("../api").AssetDetail }) {
  const attrs = Object.entries(detail.attributes);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, fontSize: 13 }}>
      {attrs.length > 0 && (
        <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", margin: 0 }}>
          {attrs.map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt className="muted">{k.replace(/_/g, " ")}</dt>
              <dd className="num" style={{ margin: 0 }}>{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {detail.sensors.length > 0 && (
        <div>
          <div className="kpi-label" style={{ marginBottom: 4 }}>Sensores</div>
          {detail.sensors.map((s) => (
            <div key={s.code}>
              <span className="num">{s.code}</span> <span className="muted">· {s.type.replace(/_/g, " ")} · {s.source}</span>
            </div>
          ))}
        </div>
      )}
      {detail.failure_modes.length > 0 && (
        <div>
          <div className="kpi-label" style={{ marginBottom: 4 }}>Modos de falla (FMEA)</div>
          {detail.failure_modes.map((f) => (
            <div key={f.code} style={{ marginBottom: 4 }}>
              {f.name} <span className="muted">· criticidad {f.criticality}/5{f.detection ? ` · ${f.detection}` : ""}</span>
            </div>
          ))}
        </div>
      )}
      {attrs.length === 0 && detail.sensors.length === 0 && detail.failure_modes.length === 0 && (
        <span className="muted">Sin datos técnicos cargados.</span>
      )}
      <span className="muted" style={{ fontSize: 12 }}>Ficha consultada {fmtDateTime(new Date().toISOString())}</span>
    </div>
  );
}
