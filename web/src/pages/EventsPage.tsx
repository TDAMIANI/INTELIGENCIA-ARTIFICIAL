import { useState } from "react";

import { api, imageUrl } from "../api";
import { Ago, ErrorBox, eventLabel, Loading } from "../components/Common";
import { StatusTag } from "../components/Status";
import { href, useResource } from "../hooks";
import { useLiveVersion } from "../live";

const TYPES = [
  { value: undefined, label: "Todas" },
  { value: "thermal_hotspot", label: "Puntos calientes" },
  { value: "person_in_zone", label: "Personas en zona" },
] as const;

export function EventsPage() {
  const eventsV = useLiveVersion("events");
  const [type, setType] = useState<string | undefined>();
  const events = useResource(() => api.events({ type, limit: 60 }), [type, eventsV]);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Detecciones del edge con imagen de evidencia</div>
          <h1>Visión</h1>
        </div>
        <div className="segmented" role="group" aria-label="Tipo de detección">
          {TYPES.map((t) => (
            <button key={t.label} aria-pressed={type === t.value} onClick={() => setType(t.value)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <ErrorBox error={events.error} />
      {!events.data ? (
        <Loading />
      ) : events.data.length === 0 ? (
        <div className="panel empty">Sin detecciones.</div>
      ) : (
        <div className="gallery">
          {events.data.map((e) => (
            <figure key={e.id} className="panel">
              {e.snapshot_url ? (
                <a href={imageUrl(e.snapshot_url)} target="_blank" rel="noreferrer">
                  <img src={imageUrl(e.snapshot_url)} alt={`Evidencia: ${e.message}`} loading="lazy" />
                </a>
              ) : (
                <div className="empty" style={{ aspectRatio: "4 / 3", display: "grid", placeItems: "center" }}>Sin imagen</div>
              )}
              <figcaption>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <strong>{eventLabel(e.type)}</strong>
                  <StatusTag status={e.severity === "critical" ? "critical" : "warning"} label={e.severity === "critical" ? "Crítico" : "Advertencia"} />
                </div>
                <div className="secondary" style={{ fontSize: 13, marginTop: 4 }}>{e.message}</div>
                <div className="item-meta">
                  <a href={href("activo", e.asset_code)}>{e.asset_code}</a>
                  <span className="num">{e.sensor_code}</span>
                  <Ago iso={e.ts} />
                </div>
              </figcaption>
            </figure>
          ))}
        </div>
      )}
    </>
  );
}
