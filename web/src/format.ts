// Formatos y reglas de presentación compartidos (sin dependencias de React: se testean aislados).

import type { Priority } from "./api";

export type Status = "good" | "warning" | "serious" | "critical" | "unknown";

/** Estado según el índice de salud. Umbrales alineados con las reglas de mantenimiento. */
export function healthStatus(hi: number | null | undefined): Status {
  if (hi === null || hi === undefined || Number.isNaN(hi)) return "unknown";
  if (hi >= 80) return "good";
  if (hi >= 60) return "warning";
  if (hi >= 40) return "serious";
  return "critical";
}

export const STATUS_LABEL: Record<Status, string> = {
  good: "Bueno",
  warning: "Atención",
  serious: "Degradado",
  critical: "Crítico",
  unknown: "Sin datos",
};

export const PRIORITY_LABEL: Record<Priority, string> = {
  urgent: "Urgente",
  high: "Alta",
  medium: "Media",
  low: "Baja",
};

export const PRIORITY_STATUS: Record<Priority, Status> = {
  urgent: "critical",
  high: "serious",
  medium: "warning",
  low: "good",
};

export const METRIC_LABEL: Record<string, { label: string; unit: string }> = {
  vibration_rms_mm_s: { label: "Vibración RMS", unit: "mm/s" },
  max_temp_c: { label: "Temperatura máx.", unit: "°C" },
  belt_edge_offset_mm: { label: "Desalineamiento", unit: "mm" },
  belt_width_mm: { label: "Ancho de banda medido", unit: "mm" },
  belt_speed_mps: { label: "Velocidad de banda", unit: "m/s" },
  motor_current_a: { label: "Corriente del motor", unit: "A" },
  load_tph: { label: "Carga", unit: "t/h" },
  running: { label: "En marcha", unit: "" },
  belt_detected: { label: "Banda detectada", unit: "" },
};

export const INDICATOR_LABEL: Record<string, string> = {
  "HI-IDL-TEMP": "Temperatura sobre vecinos",
  "HI-MOT-VIB": "Vibración (ISO 10816)",
  "HI-BELT-ALIGN": "Alineación de banda",
};

export const EVENT_LABEL: Record<string, string> = {
  thermal_hotspot: "Punto caliente",
  person_in_zone: "Persona en zona de riesgo",
};

export function metricLabel(metric: string): { label: string; unit: string } {
  return METRIC_LABEL[metric] ?? { label: metric.replace(/_/g, " "), unit: "" };
}

const nf1 = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 0 });

export function fmtNumber(v: number | null | undefined, digits: 0 | 1 = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return digits === 0 ? nf0.format(v) : nf1.format(v);
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("es-AR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });
}

/** "hace 5 min", "en 3 h", "vencida hace 2 h" — relativo a `now`. */
export function fmtRelative(iso: string, now: number = Date.now()): string {
  const diff = new Date(iso).getTime() - now;
  const abs = Math.abs(diff) / 60000;
  let text: string;
  if (abs < 1) text = "menos de 1 min";
  else if (abs < 60) text = `${Math.round(abs)} min`;
  else if (abs < 48 * 60) text = `${Math.round(abs / 60)} h`;
  else text = `${Math.round(abs / 1440)} días`;
  return diff >= 0 ? `en ${text}` : `hace ${text}`;
}

export function fmtEta(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "—";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} días`;
}

/** Número del polín a partir de su código (CV-201-IDL-12 -> 12). */
export function idlerNumber(code: string): number | null {
  const m = /-IDL-(\d+)$/.exec(code);
  return m ? Number(m[1]) : null;
}
