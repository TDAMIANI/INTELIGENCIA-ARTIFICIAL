"""Motor de alarmas de primer nivel: umbrales con confirmación e histéresis.

Es lógica pura (sin base de datos ni MQTT) para poder testearla aislada.
El motor del gemelo (Sprint 4) sumará el índice de salud sobre estas alarmas.
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from mvtwin.models import AlarmSeverity


class RuleMode(str, Enum):
    value = "value"
    abs = "abs"
    delta_neighbours = "delta_neighbours"


@dataclass(frozen=True)
class AlarmRule:
    code: str
    sensor: str
    metric: str
    warning: float
    critical: float
    clear_below: float
    mode: RuleMode = RuleMode.value
    confirm: int = 1
    neighbours: int = 3
    message: str = "{asset}: {value:.2f} (umbral {threshold})"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlarmRule":
        rule = cls(
            code=data["code"],
            sensor=data["sensor"],
            metric=data["metric"],
            warning=float(data["warning"]),
            critical=float(data["critical"]),
            clear_below=float(data.get("clear_below", data["warning"])),
            mode=RuleMode(data.get("mode", "value")),
            confirm=max(1, int(data.get("confirm", 1))),
            neighbours=max(1, int(data.get("neighbours", 3))),
            message=data.get("message", cls.message),
        )
        if not rule.clear_below <= rule.warning <= rule.critical:
            raise ValueError(f"Regla {rule.code}: se requiere clear_below <= warning <= critical")
        return rule


def load_rules(config: dict[str, Any]) -> list[AlarmRule]:
    return [AlarmRule.from_dict(r) for r in config.get("alarm_rules", [])]


class TransitionKind(str, Enum):
    raised = "raised"
    escalated = "escalated"
    cleared = "cleared"


@dataclass(frozen=True)
class AlarmTransition:
    kind: TransitionKind
    rule: AlarmRule
    asset_code: str
    severity: AlarmSeverity
    value: float
    peak_value: float
    threshold: float
    ts: datetime

    @property
    def message(self) -> str:
        return self.rule.message.format(value=self.value, threshold=self.threshold, asset=self.asset_code)


@dataclass
class _State:
    severity: AlarmSeverity | None = None  # None = sin alarma activa
    above_warning: int = 0
    above_critical: int = 0
    below_clear: int = 0
    peak: float = float("-inf")


@dataclass
class AlarmEngine:
    rules: list[AlarmRule]
    _states: dict[tuple[str, str], _State] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._by_sensor: dict[tuple[str, str], list[AlarmRule]] = {}
        for rule in self.rules:
            self._by_sensor.setdefault((rule.sensor, rule.metric), []).append(rule)

    def restore_active(self, rule_code: str, asset_code: str, severity: AlarmSeverity, peak: float) -> None:
        """Recupera una alarma activa de la base al reiniciar el servicio."""
        self._states[(rule_code, asset_code)] = _State(severity=severity, peak=peak)

    def evaluate(
        self, sensor_code: str, metric: str, ts: datetime, values: dict[str, float]
    ) -> list[AlarmTransition]:
        """Evalúa una métrica de un sensor. `values` es {código_de_activo: valor}."""
        transitions: list[AlarmTransition] = []
        for rule in self._by_sensor.get((sensor_code, metric), []):
            for asset_code, x in self._derive(rule, values).items():
                t = self._step(rule, asset_code, x, ts)
                if t is not None:
                    transitions.append(t)
        return transitions

    @staticmethod
    def _derive(rule: AlarmRule, values: dict[str, float]) -> dict[str, float]:
        if rule.mode is RuleMode.value:
            return values
        if rule.mode is RuleMode.abs:
            return {k: abs(v) for k, v in values.items()}
        # delta_neighbours: los activos se ordenan por código (p. ej. CV-201-IDL-01..40)
        codes = sorted(values)
        derived = {}
        for i, code in enumerate(codes):
            lo, hi = max(0, i - rule.neighbours), i + rule.neighbours + 1
            neighbours = [values[c] for c in codes[lo:i] + codes[i + 1 : hi]]
            if neighbours:
                derived[code] = values[code] - statistics.median(neighbours)
        return derived

    def _step(self, rule: AlarmRule, asset_code: str, x: float, ts: datetime) -> AlarmTransition | None:
        st = self._states.setdefault((rule.code, asset_code), _State())
        st.above_warning = st.above_warning + 1 if x >= rule.warning else 0
        st.above_critical = st.above_critical + 1 if x >= rule.critical else 0
        st.below_clear = st.below_clear + 1 if x < rule.clear_below else 0

        def emit(kind: TransitionKind, severity: AlarmSeverity, threshold: float) -> AlarmTransition:
            return AlarmTransition(kind, rule, asset_code, severity, round(x, 3), round(st.peak, 3), threshold, ts)

        if st.severity is None:
            if st.above_warning >= rule.confirm:
                critical = st.above_critical >= rule.confirm
                st.severity = AlarmSeverity.critical if critical else AlarmSeverity.warning
                st.peak = x
                return emit(TransitionKind.raised, st.severity, rule.critical if critical else rule.warning)
            return None

        st.peak = max(st.peak, x)
        if st.severity is AlarmSeverity.warning and st.above_critical >= rule.confirm:
            st.severity = AlarmSeverity.critical
            return emit(TransitionKind.escalated, st.severity, rule.critical)
        if st.below_clear >= rule.confirm:
            severity, st.severity = st.severity, None
            return emit(TransitionKind.cleared, severity, rule.clear_below)
        return None
