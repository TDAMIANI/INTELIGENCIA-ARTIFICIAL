// Cliente de la API de MineVision Twin. Los tipos reflejan platform/mvtwin/schemas.py.

export type AssetLevel = "plant" | "area" | "system" | "subunit" | "component";

export interface AssetSummary {
  code: string;
  name: string;
  level: AssetLevel;
  health_index: number | null;
}

export interface AssetNode extends AssetSummary {
  sensor_count: number;
  children: AssetNode[];
}

export interface Sensor {
  code: string;
  type: string;
  source: string;
  config: Record<string, unknown>;
}

export interface FailureMode {
  code: string;
  name: string;
  description: string | null;
  criticality: number;
  detection: string | null;
}

export interface AssetDetail extends AssetSummary {
  parent_code: string | null;
  attributes: Record<string, unknown>;
  children: AssetSummary[];
  sensors: Sensor[];
  failure_modes: FailureMode[];
}

export interface IndicatorResult {
  indicator: string;
  value: number;
  severity: number;
  contribution: number;
  trend_per_h: number | null;
  eta_critical_h: number | null;
  stale: boolean;
}

export interface Health {
  asset_code: string;
  asset_name: string;
  health_index: number | null;
  updated_at: string | null;
  own_health_index: number | null;
  worst_child: string | null;
  indicators: IndicatorResult[];
  children: AssetSummary[];
}

export interface HealthPoint {
  ts: string;
  health_index: number;
}

export interface TelemetryPoint {
  ts: string;
  value: number;
  min?: number | null;
  max?: number | null;
  count?: number | null;
}

export interface TelemetrySeries {
  asset_code: string;
  metric: string;
  bucket_s: number | null;
  points: TelemetryPoint[];
}

export interface LatestValue {
  asset_code: string;
  metric: string;
  ts: string;
  value: number;
}

export type AlarmSeverity = "warning" | "critical";

export interface Alarm {
  id: number;
  asset_code: string;
  asset_name: string;
  rule_code: string;
  metric: string;
  severity: AlarmSeverity;
  status: "active" | "cleared";
  message: string;
  value: number;
  peak_value: number;
  threshold: number;
  raised_at: string;
  cleared_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
}

export interface VisionEvent {
  id: number;
  ts: string;
  asset_code: string;
  sensor_code: string;
  type: string;
  severity: "info" | "warning" | "critical";
  message: string;
  value: number | null;
  has_snapshot: boolean;
  snapshot_url: string | null;
  data: Record<string, unknown>;
}

export type Priority = "urgent" | "high" | "medium" | "low";
export type RecommendationStatus = "open" | "accepted" | "dismissed" | "done";

export interface Recommendation {
  id: number;
  asset_code: string;
  asset_name: string;
  indicator: string;
  rule_code: string;
  failure_mode: string | null;
  priority: Priority;
  status: RecommendationStatus;
  action: string;
  reason: string;
  due_by: string;
  condition_active: boolean;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
  note: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | string[] | undefined | null>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) v.forEach((item) => params.append(k, item));
    else params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

let authToken: string | null = null;
export function setAuthToken(token: string | null) {
  authToken = token;
}

export async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  const res = await fetch(withQuery(path, query), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* respuesta sin JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  tree: () => request<AssetNode[]>("GET", "/api/v1/assets/tree"),
  asset: (code: string) => request<AssetDetail>("GET", `/api/v1/assets/${encodeURIComponent(code)}`),
  health: (code: string) => request<Health>("GET", `/api/v1/assets/${encodeURIComponent(code)}/health`),
  healthHistory: (code: string, start?: string) =>
    request<HealthPoint[]>("GET", `/api/v1/assets/${encodeURIComponent(code)}/health/history`, undefined, { start }),
  series: (asset: string, metric: string, start?: string, bucket?: number) =>
    request<TelemetrySeries>("GET", "/api/v1/telemetry", undefined, { asset, metric, start, bucket }),
  latest: (asset: string, subtree = false) =>
    request<LatestValue[]>("GET", "/api/v1/telemetry/latest", undefined, { asset, subtree }),
  alarms: (query: { status?: "active" | "cleared"; asset?: string; limit?: number } = {}) =>
    request<Alarm[]>("GET", "/api/v1/alarms", undefined, query),
  ackAlarm: (id: number, user: string) => request<Alarm>("POST", `/api/v1/alarms/${id}/ack`, { user }),
  events: (query: { asset?: string; type?: string; limit?: number; since?: string } = {}) =>
    request<VisionEvent[]>("GET", "/api/v1/events", undefined, query),
  recommendations: (query: { status?: RecommendationStatus[]; asset?: string } = {}) =>
    request<Recommendation[]>("GET", "/api/v1/recommendations", undefined, query),
  updateRecommendation: (id: number, status: RecommendationStatus, user: string, note?: string) =>
    request<Recommendation>("PATCH", `/api/v1/recommendations/${id}`, { status, user, note }),
};

/** URL de una imagen protegida: el token viaja como parámetro porque <img> no manda headers. */
export function imageUrl(path: string): string {
  return authToken ? `${path}?access_token=${encodeURIComponent(authToken)}` : path;
}

/** Descarga un archivo de la API (con el token de sesión) y lo guarda en el equipo. */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const res = await fetch(path, { headers: authToken ? { Authorization: `Bearer ${authToken}` } : {} });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
