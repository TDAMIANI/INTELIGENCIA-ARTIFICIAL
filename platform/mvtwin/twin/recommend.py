"""Recomendaciones de mantenimiento a partir de los indicadores de salud. Lógica pura.

Cada regla dice: "si el indicador X de un activo llega a severidad S, recomendar la
acción A con prioridad P y plazo H horas". Si la tendencia indica que el activo llega
a la zona crítica antes que ese plazo, el plazo se adelanta.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from mvtwin.twin.health import Indicator, IndicatorResult


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"

    @property
    def rank(self) -> int:
        return list(Priority).index(self)


@dataclass(frozen=True)
class MaintenanceRule:
    code: str
    indicator: str
    min_severity: float
    priority: Priority
    due_in_h: float
    action: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MaintenanceRule":
        return cls(
            code=d["code"],
            indicator=d["indicator"],
            min_severity=float(d["min_severity"]),
            priority=Priority(d["priority"]),
            due_in_h=float(d["due_in_h"]),
            action=d["action"],
        )


def load_rules(config: dict[str, Any]) -> list[MaintenanceRule]:
    return [MaintenanceRule.from_dict(d) for d in config.get("maintenance_rules", [])]


@dataclass(frozen=True)
class Proposal:
    """Recomendación deseada para (activo, indicador) en este ciclo."""

    asset_code: str
    indicator: str
    rule: MaintenanceRule
    action: str
    reason: str
    due_by: datetime
    failure_mode: str | None


def propose(
    rules: list[MaintenanceRule],
    indicators: dict[str, Indicator],
    results: dict[str, list[IndicatorResult]],
    now: datetime,
) -> list[Proposal]:
    """Para cada (activo, indicador) elige la regla más exigente que se cumple."""
    by_indicator: dict[str, list[MaintenanceRule]] = {}
    for rule in rules:
        by_indicator.setdefault(rule.indicator, []).append(rule)

    proposals = []
    for asset, asset_results in results.items():
        for r in asset_results:
            candidates = [rule for rule in by_indicator.get(r.indicator, []) if r.severity >= rule.min_severity]
            if not candidates:
                continue
            rule = max(candidates, key=lambda x: (x.priority.rank, x.min_severity))
            ind = indicators[r.indicator]
            due_h = rule.due_in_h
            reason = f"{ind.name}: {r.value:.1f}{(' ' + ind.unit) if ind.unit else ''} (severidad {r.severity:.0f}/100)"
            if r.eta_critical_h is not None:
                reason += f"; a este ritmo llega a zona crítica en ~{_hours(r.eta_critical_h)}"
                due_h = min(due_h, r.eta_critical_h)
            proposals.append(
                Proposal(
                    asset_code=asset,
                    indicator=r.indicator,
                    rule=rule,
                    action=rule.action.format(asset=asset),
                    reason=reason,
                    due_by=now + timedelta(hours=max(due_h, 0.0)),
                    failure_mode=ind.failure_mode,
                )
            )
    return proposals


def _hours(h: float) -> str:
    if h < 1:
        return f"{max(1, round(h * 60))} min"
    if h < 48:
        return f"{h:.0f} h"
    return f"{h / 24:.0f} días"
