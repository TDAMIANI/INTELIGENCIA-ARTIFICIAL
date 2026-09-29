"""Carga la jerarquía de activos desde config/plant.yaml.

Es idempotente: se puede correr varias veces y actualiza lo existente por código.

Uso:  python -m mvtwin.seed [ruta/a/plant.yaml]
"""

import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from mvtwin.db import SessionLocal
from mvtwin.models import Asset, AssetLevel, FailureMode, Sensor, SensorSource, SensorType
from mvtwin.settings import settings


def load_plant_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _expand_generated(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Expande `generate: {count, code, name, level}` en una lista de hijos."""
    gen = spec.get("generate")
    if not gen:
        return []
    return [
        {
            "code": gen["code"].format(n=n),
            "name": gen["name"].format(n=n),
            "level": gen.get("level", "component"),
            "attributes": {"position": n},
        }
        for n in range(1, int(gen["count"]) + 1)
    ]


def _upsert_asset(session: Session, spec: dict[str, Any], parent: Asset | None) -> Asset:
    asset = session.scalar(select(Asset).where(Asset.code == spec["code"]))
    if asset is None:
        asset = Asset(code=spec["code"])
        session.add(asset)
    asset.name = spec["name"]
    asset.level = AssetLevel(spec["level"])
    asset.parent = parent
    asset.attributes = spec.get("attributes") or {}

    for s in spec.get("sensors", []):
        sensor = session.scalar(select(Sensor).where(Sensor.code == s["code"]))
        if sensor is None:
            sensor = Sensor(code=s["code"])
            session.add(sensor)
        sensor.asset = asset
        sensor.type = SensorType(s["type"])
        sensor.source = SensorSource(s["source"])
        sensor.config = s.get("config") or {}

    existing_fm = {fm.code: fm for fm in asset.failure_modes}
    for f in spec.get("failure_modes", []):
        fm = existing_fm.get(f["code"])
        if fm is None:
            fm = FailureMode(code=f["code"])
            asset.failure_modes.append(fm)
        fm.name = f["name"]
        fm.description = f.get("description")
        fm.criticality = int(f.get("criticality", 3))
        fm.detection = f.get("detection")

    for child in spec.get("children", []) + _expand_generated(spec):
        _upsert_asset(session, child, asset)
    return asset


def seed(session: Session, config: dict[str, Any]) -> int:
    """Carga los activos y devuelve la cantidad total de activos en la base."""
    for root in config["assets"]:
        _upsert_asset(session, root, None)
    session.commit()
    return len(session.scalars(select(Asset.id)).all())


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.plant_config
    with SessionLocal() as session:
        total = seed(session, load_plant_config(path))
    print(f"Seed OK: {total} activos cargados desde {path}")


if __name__ == "__main__":
    main()
