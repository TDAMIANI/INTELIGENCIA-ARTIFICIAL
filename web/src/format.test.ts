import { describe, expect, it } from "vitest";

import type { Alarm, VisionEvent } from "./api";
import { fmtEta, fmtRelative, healthStatus, idlerNumber } from "./format";
import { evidenceFor } from "./pages/AlarmsPage";

describe("healthStatus", () => {
  it("usa los umbrales de las reglas de mantenimiento", () => {
    expect(healthStatus(95)).toBe("good");
    expect(healthStatus(80)).toBe("good");
    expect(healthStatus(79.9)).toBe("warning");
    expect(healthStatus(45)).toBe("serious");
    expect(healthStatus(12)).toBe("critical");
    expect(healthStatus(null)).toBe("unknown");
  });
});

describe("formatos", () => {
  const now = Date.parse("2026-01-01T12:00:00Z");
  it("expresa tiempos relativos", () => {
    expect(fmtRelative("2026-01-01T11:55:00Z", now)).toBe("hace 5 min");
    expect(fmtRelative("2026-01-01T15:00:00Z", now)).toBe("en 3 h");
    expect(fmtRelative("2026-01-05T12:00:00Z", now)).toBe("en 4 días");
  });
  it("formatea el tiempo a zona crítica", () => {
    expect(fmtEta(0.25)).toBe("15 min");
    expect(fmtEta(6)).toBe("6 h");
    expect(fmtEta(72)).toBe("3 días");
    expect(fmtEta(null)).toBe("—");
  });
  it("extrae el número de polín", () => {
    expect(idlerNumber("CV-201-IDL-07")).toBe(7);
    expect(idlerNumber("CV-201-MOT")).toBeNull();
  });
});

describe("evidenceFor", () => {
  const alarm = { asset_code: "CV-201-IDL-12", raised_at: "2026-01-01T12:00:00Z" } as Alarm;
  const ev = (id: number, asset: string, ts: string, snap = true) =>
    ({ id, asset_code: asset, ts, snapshot_url: snap ? `/s/${id}` : null }) as VisionEvent;

  it("elige la evidencia más cercana del mismo activo", () => {
    const events = [
      ev(1, "CV-201-IDL-12", "2026-01-01T11:50:00Z"),
      ev(2, "CV-201-IDL-12", "2026-01-01T12:02:00Z"),
      ev(3, "CV-201-IDL-11", "2026-01-01T12:00:00Z"),
      ev(4, "CV-201-IDL-12", "2026-01-01T12:01:00Z", false),
      ev(5, "CV-201-IDL-12", "2026-01-01T13:00:00Z"),
    ];
    expect(evidenceFor(alarm, events)?.id).toBe(2);
    expect(evidenceFor(alarm, [events[4]])).toBeUndefined();
  });
});
