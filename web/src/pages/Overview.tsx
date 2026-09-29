import { api } from "../api";
import { AssetTree, flatten } from "../components/AssetTree";
import { Ago, ErrorBox, EvidenceThumb, eventLabel, Loading, Panel } from "../components/Common";
import { HealthBadge, StatusIcon, StatusTag } from "../components/Status";
import { healthStatus, PRIORITY_LABEL, PRIORITY_STATUS, STATUS_LABEL } from "../format";
import { href, useResource } from "../hooks";
import { useLiveVersion } from "../live";

export function Overview() {
  const twinV = useLiveVersion("twin");
  const alarmsV = useLiveVersion("alarms");
  const eventsV = useLiveVersion("events");
  const tree = useResource(api.tree, [twinV]);
  const alarms = useResource(() => api.alarms({ status: "active" }), [alarmsV]);
  const recs = useResource(() => api.recommendations(), [twinV]);
  const since = new Date(Date.now() - 24 * 3600_000).toISOString();
  const events = useResource(() => api.events({ since, limit: 50 }), [eventsV]);

  const plant = tree.data?.[0];
  const all = tree.data ? flatten(tree.data) : [];
  const worst = all
    .filter((a) => (a.level === "component" || a.level === "subunit") && a.health_index !== null)
    .sort((a, b) => (a.health_index ?? 100) - (b.health_index ?? 100))
    .slice(0, 6);
  const critAlarms = alarms.data?.filter((a) => a.severity === "critical").length ?? 0;
  const urgent = recs.data?.filter((r) => r.priority === "urgent").length ?? 0;
  const high = recs.data?.filter((r) => r.priority === "high").length ?? 0;
  const plantStatus = healthStatus(plant?.health_index);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Vista general · {plant?.code ?? "planta"}</div>
          <h1>{plant?.name ?? "Planta"}</h1>
        </div>
      </div>
      <ErrorBox error={tree.error ?? alarms.error ?? recs.error} />

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <section className="panel kpi">
          <span className="kpi-bar" style={{ background: `var(--${plantStatus})` }} />
          <span className="kpi-label">Salud de la planta</span>
          <span className="kpi-value">
            {plant?.health_index === null || plant?.health_index === undefined ? "—" : Math.round(plant.health_index)}
            <small>/100</small>
          </span>
          <span className="kpi-foot">
            <StatusTag status={plantStatus} />
            <span className="muted">el peor componente manda</span>
          </span>
        </section>
        <a className="panel kpi" href={href("alarmas")} style={{ textDecoration: "none" }}>
          <span className="kpi-bar" style={{ background: critAlarms ? "var(--critical)" : "var(--line-strong)" }} />
          <span className="kpi-label">Alarmas activas</span>
          <span className="kpi-value">{alarms.data?.length ?? "—"}</span>
          <span className="kpi-foot">
            {critAlarms > 0 ? <StatusTag status="critical" label={`${critAlarms} críticas`} /> : <span className="muted">sin alarmas críticas</span>}
          </span>
        </a>
        <a className="panel kpi" href={href("recomendaciones")} style={{ textDecoration: "none" }}>
          <span className="kpi-bar" style={{ background: urgent ? "var(--critical)" : high ? "var(--serious)" : "var(--line-strong)" }} />
          <span className="kpi-label">Mantenimiento pendiente</span>
          <span className="kpi-value">{recs.data?.length ?? "—"}</span>
          <span className="kpi-foot">
            {urgent > 0 && <StatusTag status="critical" label={`${urgent} urgentes`} />}
            {high > 0 && <StatusTag status="serious" label={`${high} alta prioridad`} />}
            {!urgent && !high && <span className="muted">nada urgente</span>}
          </span>
        </a>
        <a className="panel kpi" href={href("eventos")} style={{ textDecoration: "none" }}>
          <span className="kpi-bar" style={{ background: "var(--line-strong)" }} />
          <span className="kpi-label">Detecciones de visión · 24 h</span>
          <span className="kpi-value">{events.data?.length ?? "—"}</span>
          <span className="kpi-foot muted">con imagen de evidencia</span>
        </a>
      </div>

      <div className="grid cols-main">
        <div className="grid" style={{ alignContent: "start" }}>
          <Panel title="Componentes más comprometidos" flush>
            {tree.loading && !tree.data ? (
              <Loading />
            ) : worst.length === 0 ? (
              <div className="empty">Todavía no hay índices de salud calculados.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Componente</th>
                    <th>Estado</th>
                    <th className="num">Salud</th>
                  </tr>
                </thead>
                <tbody>
                  {worst.map((a) => (
                    <tr key={a.code}>
                      <td>
                        <a href={href("activo", a.code)}>{a.name}</a> <span className="muted num" style={{ fontSize: 12 }}>{a.code}</span>
                      </td>
                      <td>
                        <StatusTag status={healthStatus(a.health_index)} />
                      </td>
                      <td className="num">
                        <HealthBadge value={a.health_index} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
          <Panel title="Activos">
            {tree.data ? <AssetTree nodes={tree.data} /> : <Loading />}
          </Panel>
        </div>

        <div className="grid" style={{ alignContent: "start" }}>
          <Panel title="Alarmas activas" actions={<a className="btn ghost" href={href("alarmas")}>Ver todas</a>} flush>
            {alarms.data && alarms.data.length === 0 ? (
              <div className="empty">Sin alarmas activas</div>
            ) : (
              <ul className="list">
                {alarms.data?.slice(0, 6).map((a) => (
                  <li key={a.id}>
                    <StatusIcon status={a.severity === "critical" ? "critical" : "warning"} />
                    <div style={{ minWidth: 0 }}>
                      <div className="item-title">{a.message}</div>
                      <div className="item-meta">
                        <a href={href("activo", a.asset_code)}>{a.asset_code}</a>
                        <Ago iso={a.raised_at} />
                        <span>{a.acknowledged_by ? `vista por ${a.acknowledged_by}` : "sin confirmar"}</span>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Mantenimiento recomendado" actions={<a className="btn ghost" href={href("recomendaciones")}>Ver todo</a>} flush>
            {recs.data && recs.data.length === 0 ? (
              <div className="empty">Sin recomendaciones pendientes</div>
            ) : (
              <ul className="list">
                {recs.data?.slice(0, 5).map((r) => (
                  <li key={r.id}>
                    <StatusIcon status={PRIORITY_STATUS[r.priority]} />
                    <div style={{ minWidth: 0 }}>
                      <div className="item-title">{r.action}</div>
                      <div className="item-meta">
                        <span>{PRIORITY_LABEL[r.priority]}</span>
                        <span>vence <Ago iso={r.due_by} /></span>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Últimas detecciones" actions={<a className="btn ghost" href={href("eventos")}>Galería</a>} flush>
            {events.data && events.data.length === 0 ? (
              <div className="empty">Sin detecciones en las últimas 24 h</div>
            ) : (
              <ul className="list">
                {events.data?.slice(0, 3).map((e) => (
                  <li key={e.id}>
                    <EvidenceThumb event={e} size={104} />
                    <div style={{ minWidth: 0 }}>
                      <div className="item-title">{eventLabel(e.type)}</div>
                      <div className="secondary" style={{ fontSize: 13 }}>{e.message}</div>
                      <div className="item-meta">
                        <span>{STATUS_LABEL[e.severity === "critical" ? "critical" : "warning"]}</span>
                        <Ago iso={e.ts} />
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
