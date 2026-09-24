"""Modelo de datos del gemelo digital: jerarquía de activos, sensores y modos de falla.

La jerarquía sigue la idea de ISO 14224 (planta > área > sistema > subunidad > componente)
y se representa como un árbol autorreferenciado en la tabla `assets`.
"""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mvtwin.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


class AssetLevel(str, enum.Enum):
    plant = "plant"
    area = "area"
    system = "system"
    subunit = "subunit"
    component = "component"


class SensorType(str, enum.Enum):
    rgb_camera = "rgb_camera"
    thermal_camera = "thermal_camera"
    vibration = "vibration"
    temperature = "temperature"
    plc_tags = "plc_tags"


class SensorSource(str, enum.Enum):
    edge = "edge"  # publicado por el gateway edge (visión, vibración)
    plc = "plc"  # leído del PLC/SCADA vía OPC UA
    manual = "manual"  # inspecciones cargadas a mano


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, native_enum=False, length=32, validate_strings=True)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    level: Mapped[AssetLevel] = mapped_column(_enum(AssetLevel, "asset_level"))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True, default=None
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # Índice de salud 0-100; lo calcula el motor del gemelo. None = sin datos.
    health_index: Mapped[float | None] = mapped_column(default=None)
    # Explicación del HI: resultado de cada indicador (valor, severidad, tendencia...).
    health_details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    health_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent: Mapped["Asset | None"] = relationship(back_populates="children", remote_side=[id])
    children: Mapped[list["Asset"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", order_by="Asset.code"
    )
    sensors: Mapped[list["Sensor"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="Sensor.code"
    )
    failure_modes: Mapped[list["FailureMode"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="FailureMode.code"
    )


class Sensor(Base):
    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    type: Mapped[SensorType] = mapped_column(_enum(SensorType, "sensor_type"))
    source: Mapped[SensorSource] = mapped_column(_enum(SensorSource, "sensor_source"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    asset: Mapped[Asset] = relationship(back_populates="sensors")


class FailureMode(Base):
    """Modo de falla del FMEA asociado a un activo."""

    __tablename__ = "failure_modes"
    __table_args__ = (UniqueConstraint("asset_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # 1 (bajo) a 5 (crítico)
    criticality: Mapped[int] = mapped_column(Integer, default=3)
    detection: Mapped[str | None] = mapped_column(String(200), default=None)

    asset: Mapped[Asset] = relationship(back_populates="failure_modes")


class Telemetry(Base):
    """Una medición: formato "largo" (una fila por activo, métrica e instante).

    En PostgreSQL con TimescaleDB es una hypertable particionada por `ts` (ver migración 0002).
    La clave primaria empieza por (asset_id, metric, ts), que es la consulta típica de tendencias.
    """

    __tablename__ = "telemetry"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    metric: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[float] = mapped_column(Float)


class AlarmSeverity(str, enum.Enum):
    warning = "warning"
    critical = "critical"


class AlarmStatus(str, enum.Enum):
    active = "active"
    cleared = "cleared"


class Alarm(Base):
    """Alarma de condición. Hay como máximo una alarma activa por (activo, regla)."""

    __tablename__ = "alarms"
    __table_args__ = (Index("ix_alarms_status_raised_at", "status", "raised_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    sensor_id: Mapped[int | None] = mapped_column(ForeignKey("sensors.id", ondelete="SET NULL"), default=None)
    rule_code: Mapped[str] = mapped_column(String(64))
    metric: Mapped[str] = mapped_column(String(64))
    severity: Mapped[AlarmSeverity] = mapped_column(_enum(AlarmSeverity, "alarm_severity"))
    status: Mapped[AlarmStatus] = mapped_column(_enum(AlarmStatus, "alarm_status"), default=AlarmStatus.active)
    message: Mapped[str] = mapped_column(String(300))
    # Valor que disparó la alarma y valor máximo observado mientras estuvo activa.
    value: Mapped[float] = mapped_column(Float)
    peak_value: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    acknowledged_by: Mapped[str | None] = mapped_column(String(100), default=None)

    asset: Mapped[Asset] = relationship()
    sensor: Mapped[Sensor | None] = relationship()


class EventSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class Event(Base):
    """Evento detectado por el edge (visión), con su imagen de evidencia en S3."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_asset_ts", "asset_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[EventSeverity] = mapped_column(_enum(EventSeverity, "event_severity"))
    message: Mapped[str] = mapped_column(String(300))
    value: Mapped[float | None] = mapped_column(Float, default=None)
    snapshot_bucket: Mapped[str | None] = mapped_column(String(100), default=None)
    snapshot_key: Mapped[str | None] = mapped_column(String(500), default=None)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    asset: Mapped[Asset] = relationship()
    sensor: Mapped[Sensor] = relationship()


class HealthHistory(Base):
    """Evolución del índice de salud (hypertable en TimescaleDB)."""

    __tablename__ = "health_history"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    health_index: Mapped[float] = mapped_column(Float)


class RecommendationStatus(str, enum.Enum):
    open = "open"  # nueva, sin revisar
    accepted = "accepted"  # planificada (se generó la orden de trabajo)
    dismissed = "dismissed"  # descartada por el planificador
    done = "done"  # trabajo realizado


class Recommendation(Base):
    """Recomendación de mantenimiento generada por el gemelo digital."""

    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_recommendations_asset_indicator", "asset_id", "indicator"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    failure_mode_id: Mapped[int | None] = mapped_column(
        ForeignKey("failure_modes.id", ondelete="SET NULL"), default=None
    )
    indicator: Mapped[str] = mapped_column(String(64))
    rule_code: Mapped[str] = mapped_column(String(64))
    priority: Mapped[str] = mapped_column(String(16))
    status: Mapped[RecommendationStatus] = mapped_column(
        _enum(RecommendationStatus, "recommendation_status"), default=RecommendationStatus.open, index=True
    )
    action: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(String(500))
    due_by: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # False cuando el indicador volvió a la normalidad (la recomendación no se cierra sola).
    condition_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(100), default=None)
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    # Número de la orden de trabajo / aviso en el CMMS (SAP PM, Maximo...), si se envió.
    work_order_ref: Mapped[str | None] = mapped_column(String(100), default=None)

    asset: Mapped[Asset] = relationship()
    failure_mode: Mapped[FailureMode | None] = relationship()
