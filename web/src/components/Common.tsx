import type { ReactNode } from "react";

import { imageUrl, type VisionEvent } from "../api";
import { EVENT_LABEL, fmtRelative } from "../format";

export function Panel({ title, actions, children, flush = false }: { title: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        {actions}
      </div>
      <div className={flush ? "panel-body flush" : "panel-body"}>{children}</div>
    </section>
  );
}

export function ErrorBox({ error }: { error?: Error }) {
  if (!error) return null;
  return <div className="error-box">No se pudieron cargar los datos: {error.message}</div>;
}

export function Loading() {
  return <div className="empty">Cargando…</div>;
}

export function EvidenceThumb({ event, size = 132 }: { event: VisionEvent; size?: number }) {
  if (!event.snapshot_url) return null;
  return (
    <a href={imageUrl(event.snapshot_url)} target="_blank" rel="noreferrer" title="Abrir la imagen de evidencia">
      <img className="thumb" style={{ width: size }} src={imageUrl(event.snapshot_url)} alt={`Evidencia: ${event.message}`} loading="lazy" />
    </a>
  );
}

export function eventLabel(type: string): string {
  return EVENT_LABEL[type] ?? type.replace(/_/g, " ");
}

export function Ago({ iso }: { iso: string }) {
  return <time dateTime={iso} title={new Date(iso).toLocaleString("es-AR")}>{fmtRelative(iso)}</time>;
}
