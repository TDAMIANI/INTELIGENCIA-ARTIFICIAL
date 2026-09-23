"""Parseo de mensajes de telemetría MQTT (formato definido en simulator/mvsim/messages.py)."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime


class InvalidMessage(ValueError):
    pass


@dataclass(frozen=True)
class TelemetryMessage:
    sensor_code: str
    ts: datetime
    # {métrica: {código_de_activo | None: valor}}; None = el activo del propio sensor.
    metrics: dict[str, dict[str | None, float]]


def parse_telemetry(topic: str, payload: bytes | str) -> TelemetryMessage:
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "mvt" or parts[3] != "telemetry":
        raise InvalidMessage(f"Tópico inesperado: {topic}")
    sensor_code = parts[2]
    try:
        data = json.loads(payload)
        ts = datetime.fromisoformat(data["ts"])
        values = data["values"]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidMessage(f"Payload inválido en {topic}: {exc}") from exc
    if not isinstance(values, dict):
        raise InvalidMessage(f"'values' debe ser un objeto en {topic}")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    metrics: dict[str, dict[str | None, float]] = {}
    for metric, value in values.items():
        if isinstance(value, dict):
            per_asset = {code: _number(v) for code, v in value.items()}
            per_asset = {code: v for code, v in per_asset.items() if v is not None}
            if per_asset:
                metrics[metric] = per_asset
        elif (number := _number(value)) is not None:
            metrics[metric] = {None: number}
    return TelemetryMessage(sensor_code, ts, metrics)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    return None
