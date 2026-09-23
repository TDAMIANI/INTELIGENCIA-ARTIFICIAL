from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mvtwin.models import AssetLevel, SensorSource, SensorType


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
