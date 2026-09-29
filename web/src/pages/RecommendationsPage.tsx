import { useState } from "react";

import { api, downloadFile, type Recommendation, type RecommendationStatus } from "../api";
import { Ago, ErrorBox, Loading, Panel } from "../components/Common";
import { StatusTag } from "../components/Status";
import { fmtDateTime, PRIORITY_LABEL, PRIORITY_STATUS } from "../format";
import { href, useResource } from "../hooks";
import { useLiveVersion } from "../live";
import { useSession } from "../session";

const STATUS_LABEL: Record<RecommendationStatus, string> = {
  open: "Nueva",
  accepted: "Planificada",
  dismissed: "Descartada",
  done: "Realizada",
};

export function RecommendationsPage() {
  const [view, setView] = useState<"pending" | "closed">("pending");
  const twinV = useLiveVersion("twin");
  const statuses: RecommendationStatus[] = view === "pending" ? ["open", "accepted"] : ["done", "dismissed"];
  const recs = useResource(() => api.recommendations({ status: statuses }), [view, twinV]);
  const { user, can } = useSession();
  const [error, setError] = useState<Error>();

  const update = async (r: Recommendation, status: RecommendationStatus, note?: string) => {
    if (!user) return;
    try {
      await api.updateRecommendation(r.id, status, user.name, note);
      recs.reload();
    } catch (e) {
      setError(e as Error);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Plan de mantenimiento sugerido por el gemelo</div>
          <h1>Recomendaciones</h1>
        </div>
        <div className="btn-row">
          {can("planner") && (
            <button
              className="btn ghost"
              onClick={() =>
                downloadFile("/api/v1/work-orders/export.csv", `ordenes-de-trabajo-${new Date().toISOString().slice(0, 10)}.csv`).catch(setError)
              }
            >
              Exportar órdenes de trabajo (CSV)
            </button>
          )}
          <div className="segmented" role="group" aria-label="Filtro">
            <button aria-pressed={view === "pending"} onClick={() => setView("pending")}>Pendientes</button>
            <button aria-pressed={view === "closed"} onClick={() => setView("closed")}>Cerradas</button>
          </div>
        </div>
      </div>
      <ErrorBox error={recs.error ?? error} />
      <Panel title={view === "pending" ? "Pendientes, por prioridad y plazo" : "Cerradas"} flush>
        {!recs.data ? (
          <Loading />
        ) : recs.data.length === 0 ? (
          <div className="empty">{view === "pending" ? "No hay trabajos recomendados." : "Sin recomendaciones cerradas."}</div>
        ) : (
          <ul className="list">
            {recs.data.map((r) => (
              <RecommendationItem key={r.id} rec={r} canEdit={can("planner")} onUpdate={update} />
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}

function RecommendationItem({
  rec: r,
  canEdit,
  onUpdate,
}: {
  rec: Recommendation;
  canEdit: boolean;
  onUpdate: (r: Recommendation, status: RecommendationStatus, note?: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [dismissing, setDismissing] = useState(false);
  const overdue = new Date(r.due_by).getTime() < Date.now();
  const pending = r.status === "open" || r.status === "accepted";

  return (
    <li style={{ flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <StatusTag status={PRIORITY_STATUS[r.priority]} label={PRIORITY_LABEL[r.priority]} />
        <span className="muted" style={{ fontSize: 12 }}>{STATUS_LABEL[r.status]}</span>
        {pending && (
          <span style={{ fontSize: 12, fontWeight: overdue ? 700 : 400, color: overdue ? "var(--critical)" : "var(--text-secondary)" }} title={fmtDateTime(r.due_by)}>
            {overdue ? "Vencida " : "Vence "}
            <Ago iso={r.due_by} />
          </span>
        )}
        {!r.condition_active && pending && <span className="muted" style={{ fontSize: 12 }}>· la condición se normalizó</span>}
      </div>
      <div className="item-title" style={{ fontSize: 15 }}>{r.action}</div>
      <div className="secondary" style={{ fontSize: 13 }}>{r.reason}</div>
      <div className="item-meta">
        <a href={href("activo", r.asset_code)}>{r.asset_name} · {r.asset_code}</a>
        {r.failure_mode && <span>Modo de falla: {r.failure_mode}</span>}
        <span>Creada <Ago iso={r.created_at} /></span>
        {r.updated_by && <span>Última acción: {r.updated_by}</span>}
        {r.note && <span>Nota: {r.note}</span>}
      </div>
      {canEdit && pending && (
        <div className="btn-row" style={{ marginTop: 4 }}>
          {r.status === "open" && (
            <button className="btn primary" onClick={() => onUpdate(r, "accepted")}>
              Planificar (generar OT)
            </button>
          )}
          <button className="btn" onClick={() => onUpdate(r, "done")}>Marcar realizada</button>
          {dismissing ? (
            <>
              <input className="input" placeholder="Motivo del descarte" value={note} onChange={(e) => setNote(e.target.value)} style={{ minWidth: 220 }} />
              <button className="btn" disabled={!note.trim()} onClick={() => onUpdate(r, "dismissed", note.trim())}>
                Confirmar descarte
              </button>
              <button className="btn ghost" onClick={() => setDismissing(false)}>Cancelar</button>
            </>
          ) : (
            <button className="btn ghost" onClick={() => setDismissing(true)}>Descartar</button>
          )}
        </div>
      )}
    </li>
  );
}
