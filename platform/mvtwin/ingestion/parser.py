"""Parseo de mensajes de telemetría MQTT (formato definido en simulator/mvsim/messages.py)."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class InvalidMessage(ValueError):
    pass


@dataclass(frozen=True)
class TelemetryMessage:
    sensor_code: str
    ts: datetime
    # {métrica: {código_de_activo | None: valor}}; None = el activo del propio sensor.
    metrics: dict[str, dict[str | None, float]]


@dataclass(frozen=True)
class EventMessage:
    sensor_code: str
    ts: datetime
    type: str
    severity: str
    message: str
    asset_code: str | None = None  # None = el activo del propio sensor
    value: float | None = None
    snapshot_bucket: str | None = None
    snapshot_key: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def _split(topic: str, kind: str) -> str:
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "mvt" or parts[3] != kind:
        raise InvalidMessage(f"Tópico inesperado: {topic}")
    return parts[2]


def _load(topic: str, payload: bytes | str) -> tuple[dict[str, Any], datetime]:
    try:
        data = json.loads(payload)
        ts = datetime.fromisoformat(data["ts"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidMessage(f"Payload inválido en {topic}: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidMessage(f"Payload inválido en {topic}")
    return data, ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def parse_event(topic: str, payload: bytes | str) -> EventMessage:
    sensor_code = _split(topic, "event")
    data, ts = _load(topic, payload)
    try:
        snapshot = data.get("snapshot") or {}
        value = data.get("value")
        return EventMessage(
            sensor_code=sensor_code,
            ts=ts,
            type=str(data["type"])[:64],
            severity=data.get("severity", "warning"),
            message=str(data.get("message", ""))[:300],
            asset_code=data.get("asset"),
            value=_number(value) if value is not None else None,
            snapshot_bucket=snapshot.get("bucket"),
            snapshot_key=snapshot.get("key"),
            data=data.get("data") or {},
        )
    except (KeyError, AttributeError, TypeError) as exc:
        raise InvalidMessage(f"Evento inválido en {topic}: {exc}") from exc


def parse_telemetry(topic: str, payload: bytes | str) -> TelemetryMessage:
    sensor_code = _split(topic, "telemetry")
    data, ts = _load(topic, payload)
    values = data.get("values")
    if not isinstance(values, dict):
        raise InvalidMessage(f"'values' debe ser un objeto en {topic}")

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
