import { useState } from "react";

import { api, type Alarm, type VisionEvent } from "../api";
import { Ago, ErrorBox, EvidenceThumb, Loading, Panel } from "../components/Common";
import { StatusTag } from "../components/Status";
import { fmtDateTime, fmtNumber } from "../format";
import { href, useResource } from "../hooks";
import { useLiveVersion } from "../live";
import { useSession } from "../session";

/** Evidencia de visión del mismo activo dentro de ±15 min del disparo de la alarma. */
export function evidenceFor(alarm: Alarm, events: VisionEvent[]): VisionEvent | undefined {
  const t = new Date(alarm.raised_at).getTime();
  return events
    .filter((e) => e.asset_code === alarm.asset_code && e.snapshot_url && Math.abs(new Date(e.ts).getTime() - t) <= 15 * 60_000)
    .sort((a, b) => Math.abs(new Date(a.ts).getTime() - t) - Math.abs(new Date(b.ts).getTime() - t))[0];
}

export function AlarmsPage() {
  const [view, setView] = useState<"active" | "cleared">("active");
  const alarmsV = useLiveVersion("alarms");
  const eventsV = useLiveVersion("events");
  const alarms = useResource(() => api.alarms({ status: view, limit: 200 }), [view, alarmsV]);
  const events = useResource(() => api.events({ limit: 200 }), [eventsV]);
  const { user, can } = useSession();
  const [busy, setBusy] = useState<number>();
  const [error, setError] = useState<Error>();

  const ack = async (id: number) => {
    if (!user) return;
    setBusy(id);
    try {
      await api.ackAlarm(id, user.name);
      alarms.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Centro de alarmas</div>
          <h1>Alarmas</h1>
        </div>
        <div className="segmented" role="group" aria-label="Filtro">
          <button aria-pressed={view === "active"} onClick={() => setView("active")}>Activas</button>
          <button aria-pressed={view === "cleared"} onClick={() => setView("cleared")}>Normalizadas</button>
        </div>
      </div>
      <ErrorBox error={alarms.error ?? error} />
      <Panel title={view === "active" ? "Activas" : "Historial"} flush>
        {!alarms.data ? (
          <Loading />
        ) : alarms.data.length === 0 ? (
          <div className="empty">{view === "active" ? "Sin alarmas activas. Todo en orden." : "Sin alarmas normalizadas."}</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Severidad</th>
                <th>Alarma</th>
                <th className="num">Pico</th>
                <th>Evidencia</th>
                <th>Operador</th>
              </tr>
            </thead>
            <tbody>
              {alarms.data.map((a) => {
                const ev = events.data ? evidenceFor(a, events.data) : undefined;
                return (
                  <tr key={a.id}>
                    <td>
                      <StatusTag status={a.severity === "critical" ? "critical" : "warning"} label={a.severity === "critical" ? "Crítica" : "Advertencia"} />
                    </td>
                    <td>
                      <div className="item-title">{a.message}</div>
                      <div className="item-meta">
                        <a href={href("activo", a.asset_code)}>{a.asset_name} · {a.asset_code}</a>
                        <span title={fmtDateTime(a.raised_at)}>
                          disparada <Ago iso={a.raised_at} />
                        </span>
                        {a.cleared_at && <span>normalizada <Ago iso={a.cleared_at} /></span>}
                        <span className="num">{a.rule_code}</span>
                      </div>
                    </td>
                    <td className="num">{fmtNumber(a.peak_value)}</td>
                    <td>{ev ? <EvidenceThumb event={ev} size={112} /> : <span className="muted">—</span>}</td>
                    <td>
                      {a.acknowledged_by ? (
                        <span className="secondary" style={{ fontSize: 13 }}>
                          Vista por {a.acknowledged_by}
                          <br />
                          <span className="muted"><Ago iso={a.acknowledged_at!} /></span>
                        </span>
                      ) : can("operator") && a.status === "active" ? (
                        <button className="btn primary" disabled={busy === a.id} onClick={() => ack(a.id)}>
                          Confirmar
                        </button>
                      ) : (
                        <span className="muted">sin confirmar</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
