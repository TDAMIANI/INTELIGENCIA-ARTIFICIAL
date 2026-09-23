"""Modelo de datos del gemelo digital: jerarquía de activos, sensores y modos de falla.

La jerarquía sigue la idea de ISO 14224 (planta > área > sistema > subunidad > componente)
y se representa como un árbol autorreferenciado en la tabla `assets`.
"""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    # Índice de salud 0-100; lo calcula el motor del gemelo (Sprint 4). None = sin datos.
    health_index: Mapped[float | None] = mapped_column(default=None)
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
