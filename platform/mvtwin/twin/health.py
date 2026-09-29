"""Índice de salud (Health Index, HI) de los activos. Lógica pura, sin base de datos.

    HI_componente = 100 − Σ (peso_i × severidad_i)      (acotado a 0..100)

Cada indicador convierte un valor medido (p. ej. ΔT de un polín) en una severidad de
0 a 100 con una curva por tramos definida en config/plant.yaml, basada en normas
(ISO 10816) o en el criterio de los mantenedores. Es simple y explicable: el HI se
puede justificar indicador por indicador.

Los activos padres (subunidad, sistema, área, planta) toman el HI de su peor hijo:
una correa está tan sana como su polín más comprometido.

Además, con la tendencia de la última hora se estima en cuántas horas el indicador
llegaría a la zona crítica (una primera aproximación a la vida útil remanente, RUL).
"""

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class IndicatorMode(str, Enum):
    value = "value"
    abs = "abs"
    delta_neighbours = "delta_neighbours"


@dataclass(frozen=True)
class Context:
    """Condición operativa para evaluar un indicador (p. ej. correa en marcha)."""

    sensor: str
    metric: str
    min: float


@dataclass(frozen=True)
class Indicator:
    code: str
    name: str
    sensor: str
    metric: str
    curve: tuple[tuple[float, float], ...]  # (valor, severidad), valores crecientes
    mode: IndicatorMode = IndicatorMode.value
    neighbours: int = 3
    weight: float = 1.0
    unit: str = ""
    failure_mode: str | None = None
    context: Context | None = None
    # Severidad a partir de la cual se considera "crítico" (para estimar el tiempo restante).
    critical_severity: float = 80.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Indicator":
        curve = tuple((float(v), float(s)) for v, s in d["curve"])
        if len(curve) < 2 or any(b[0] <= a[0] for a, b in zip(curve, curve[1:])):
            raise ValueError(f"Indicador {d['code']}: la curva necesita 2+ puntos con valores crecientes")
        if any(not 0 <= s <= 100 for _, s in curve):
            raise ValueError(f"Indicador {d['code']}: la severidad va de 0 a 100")
        ctx = d.get("context")
        return cls(
            code=d["code"],
            name=d.get("name", d["code"]),
            sensor=d["sensor"],
            metric=d["metric"],
            curve=curve,
            mode=IndicatorMode(d.get("mode", "value")),
            neighbours=int(d.get("neighbours", 3)),
            weight=float(d.get("weight", 1.0)),
            unit=d.get("unit", ""),
            failure_mode=d.get("failure_mode"),
            context=Context(ctx["sensor"], ctx["metric"], float(ctx.get("min", 0.5))) if ctx else None,
            critical_severity=float(d.get("critical_severity", 80.0)),
        )

    def severity(self, value: float) -> float:
        """Interpolación lineal por tramos; fuera de la curva se toma el extremo."""
        pts = self.curve
        if value <= pts[0][0]:
            return pts[0][1]
        if value >= pts[-1][0]:
            return pts[-1][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= value <= x1:
                return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
        return pts[-1][1]  # pragma: no cover

    def critical_value(self) -> float | None:
        """Valor medido a partir del cual la severidad alcanza `critical_severity`."""
        for (x0, y0), (x1, y1) in zip(self.curve, self.curve[1:]):
            if y0 < self.critical_severity <= y1:
                return x0 + (x1 - x0) * (self.critical_severity - y0) / (y1 - y0)
        return None

    def derive(self, values: dict[str, float]) -> dict[str, float]:
        """Transforma {activo: valor medido} en {activo: valor del indicador}."""
        if self.mode is IndicatorMode.value:
            return dict(values)
        if self.mode is IndicatorMode.abs:
            return {k: abs(v) for k, v in values.items()}
        codes = sorted(values)
        out = {}
        for i, code in enumerate(codes):
            near = [values[c] for c in codes[max(0, i - self.neighbours) : i] + codes[i + 1 : i + self.neighbours + 1]]
            if near:
                out[code] = values[code] - statistics.median(near)
        return out


def load_indicators(config: dict[str, Any]) -> list[Indicator]:
    return [Indicator.from_dict(d) for d in config.get("health_indicators", [])]


@dataclass(frozen=True)
class Trend:
    per_hour: float
    r2: float
    eta_critical_h: float | None  # None: no se acerca a la zona crítica


def linear_trend(points: list[tuple[datetime, float]], current: float, critical: float | None,
                 min_points: int = 5, min_r2: float = 0.5) -> Trend | None:
    """Recta de mínimos cuadrados sobre la serie; estima cuándo llega al valor crítico."""
    if len(points) < min_points:
        return None
    t0 = points[0][0]
    xs = [(ts - t0).total_seconds() / 3600 for ts, _ in points]
    ys = [v for _, v in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    eta = None
    if critical is not None and slope > 0 and r2 >= min_r2 and current < critical:
        eta = (critical - current) / slope
    return Trend(per_hour=round(slope, 3), r2=round(r2, 3), eta_critical_h=round(eta, 1) if eta is not None else None)


@dataclass(frozen=True)
class IndicatorResult:
    indicator: str
    value: float
    severity: float
    contribution: float
    trend_per_h: float | None = None
    eta_critical_h: float | None = None
    stale: bool = False  # True si se reutilizó el último valor (p. ej. correa detenida)

    def to_dict(self) -> dict[str, Any]:
        return {
            "indicator": self.indicator,
            "value": round(self.value, 2),
            "severity": round(self.severity, 1),
            "contribution": round(self.contribution, 1),
            "trend_per_h": self.trend_per_h,
            "eta_critical_h": self.eta_critical_h,
            "stale": self.stale,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IndicatorResult":
        return cls(
            indicator=d["indicator"],
            value=float(d["value"]),
            severity=float(d["severity"]),
            contribution=float(d["contribution"]),
            trend_per_h=d.get("trend_per_h"),
            eta_critical_h=d.get("eta_critical_h"),
            stale=True,
        )


def evaluate(indicator: Indicator, values: dict[str, float],
             trends: dict[str, list[tuple[datetime, float]]] | None = None) -> dict[str, IndicatorResult]:
    """Evalúa un indicador para todos los activos que mide. `trends` = series crudas por activo."""
    derived = indicator.derive(values)
    derived_series = _derive_series(indicator, trends) if trends else {}
    critical = indicator.critical_value()
    out = {}
    for asset, value in derived.items():
        sev = indicator.severity(value)
        trend = linear_trend(derived_series.get(asset, []), value, critical)
        out[asset] = IndicatorResult(
            indicator=indicator.code,
            value=value,
            severity=sev,
            contribution=indicator.weight * sev,
            trend_per_h=trend.per_hour if trend else None,
            eta_critical_h=trend.eta_critical_h if trend else None,
        )
    return out


def _derive_series(indicator: Indicator, series: dict[str, list[tuple[datetime, float]]]):
    """Aplica `derive` en cada instante (necesario para ΔT contra vecinos)."""
    by_ts: dict[datetime, dict[str, float]] = {}
    for asset, points in series.items():
        for ts, v in points:
            by_ts.setdefault(ts, {})[asset] = v
    out: dict[str, list[tuple[datetime, float]]] = {}
    for ts in sorted(by_ts):
        for asset, v in indicator.derive(by_ts[ts]).items():
            out.setdefault(asset, []).append((ts, v))
    return out


def health_index(results: list[IndicatorResult]) -> float | None:
    if not results:
        return None
    return round(max(0.0, 100.0 - sum(r.contribution for r in results)), 1)


def rollup(own: dict[str, float | None], children: dict[str, list[str]], root: str) -> dict[str, float | None]:
    """HI de cada activo = mínimo entre el propio y el de sus descendientes (el peor manda)."""
    out: dict[str, float | None] = {}

    def visit(code: str) -> float | None:
        values = [own.get(code)] + [visit(c) for c in children.get(code, [])]
        present = [v for v in values if v is not None and not math.isnan(v)]
        out[code] = min(present) if present else None
        return out[code]

    visit(root)
    return out
