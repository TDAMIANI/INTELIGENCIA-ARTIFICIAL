from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from mvtwin.models import AlarmSeverity, AlarmStatus, AssetLevel, EventSeverity, SensorSource, SensorType

if TYPE_CHECKING:
    from mvtwin.models import Alarm, Event


class SensorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    type: SensorType
    source: SensorSource
    config: dict[str, Any]


class FailureModeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None
    criticality: int
    detection: str | None


class AssetSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    level: AssetLevel
    health_index: float | None


class AssetDetail(AssetSummary):
    parent_code: str | None
    attributes: dict[str, Any]
    children: list[AssetSummary]
    sensors: list[SensorOut]
    failure_modes: list[FailureModeOut]


class AssetNode(AssetSummary):
    sensor_count: int
    children: list["AssetNode"]


class AssetCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    level: AssetLevel
    parent_code: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    attributes: dict[str, Any] | None = None


class TelemetryPoint(BaseModel):
    ts: datetime
    value: float
    # Solo en consultas agregadas por intervalo (bucket).
    min: float | None = None
    max: float | None = None
    count: int | None = None


class TelemetrySeries(BaseModel):
    asset_code: str
    metric: str
    bucket_s: int | None
    points: list[TelemetryPoint]


class LatestValue(BaseModel):
    asset_code: str
    metric: str
    ts: datetime
    value: float


class AlarmOut(BaseModel):
    id: int
    asset_code: str
    asset_name: str
    rule_code: str
    metric: str
    severity: AlarmSeverity
    status: AlarmStatus
    message: str
    value: float
    peak_value: float
    threshold: float
    raised_at: datetime
    cleared_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: str | None

    @classmethod
    def from_model(cls, alarm: "Alarm") -> "AlarmOut":
        return cls(
            id=alarm.id,
            asset_code=alarm.asset.code,
            asset_name=alarm.asset.name,
            rule_code=alarm.rule_code,
            metric=alarm.metric,
            severity=alarm.severity,
            status=alarm.status,
            message=alarm.message,
            value=alarm.value,
            peak_value=alarm.peak_value,
            threshold=alarm.threshold,
            raised_at=alarm.raised_at,
            cleared_at=alarm.cleared_at,
            acknowledged_at=alarm.acknowledged_at,
            acknowledged_by=alarm.acknowledged_by,
        )


class AlarmAck(BaseModel):
    user: str = Field(min_length=1, max_length=100)


class EventOut(BaseModel):
    id: int
    ts: datetime
    asset_code: str
    sensor_code: str
    type: str
    severity: EventSeverity
    message: str
    value: float | None
    has_snapshot: bool
    snapshot_url: str | None
    data: dict[str, Any]

    @classmethod
    def from_model(cls, event: "Event") -> "EventOut":
        has_snapshot = bool(event.snapshot_key)
        return cls(
            id=event.id,
            ts=event.ts,
            asset_code=event.asset.code,
            sensor_code=event.sensor.code,
            type=event.type,
            severity=event.severity,
            message=event.message,
            value=event.value,
            has_snapshot=has_snapshot,
            snapshot_url=f"/api/v1/events/{event.id}/snapshot" if has_snapshot else None,
            data=event.data,
        )
