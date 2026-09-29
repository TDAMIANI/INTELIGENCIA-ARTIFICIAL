from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mvtwin.models import Asset, HealthHistory, Recommendation, Sensor, Telemetry
from mvtwin.seed import load_plant_config
from mvtwin.settings import settings
from mvtwin.twin.service import build_engine

NOW = datetime.now(UTC).replace(microsecond=0)


def write_hour(factory: sessionmaker[Session], end: datetime, hot_delta_end: float = 25.0,
               vibration: float = 1.6, running: float = 1.0, minutes: int = 60) -> None:
    """Una hora de telemetría (una muestra por minuto): el polín 12 se calienta linealmente."""
    with factory() as s:
        ids = dict(s.execute(select(Asset.code, Asset.id)).tuples().all())
        sensors = dict(s.execute(select(Sensor.code, Sensor.id)).tuples().all())
        rows = []
        for m in range(minutes):
            ts = end - timedelta(minutes=minutes - m) + timedelta(seconds=30)
            frac = m / (minutes - 1)
            rows.append(Telemetry(asset_id=ids["CV-201"], sensor_id=sensors["CV-201-PLC"],
                                  metric="running", ts=ts, value=running))
            rows.append(Telemetry(asset_id=ids["CV-201-MOT"], sensor_id=sensors["CV-201-MOT-VIB"],
                                  metric="vibration_rms_mm_s", ts=ts, value=vibration))
            for n in range(1, 41):
                temp = 30.0 + (5.0 + frac * (hot_delta_end - 5.0) if n == 12 else 0.0)
                rows.append(Telemetry(asset_id=ids[f"CV-201-IDL-{n:02d}"], sensor_id=sensors["CV-201-TH-01"],
                                      metric="max_temp_c", ts=ts, value=temp))
        s.add_all(rows)
        s.commit()


@pytest.fixture
def engine(session_factory):
    published: list[dict] = []
    eng = build_engine(session_factory, lambda _t, p: published.append(p), load_plant_config(settings.plant_config))
    eng.published = published
    return eng


def test_cycle_computes_health_and_recommendation(session_factory, engine) -> None:
    write_hour(session_factory, NOW)
    hi = engine.cycle(NOW)

    assert hi["CV-201-IDL-12"] < 45  # ΔT ≈ 25 °C -> severidad ≈ 67
    assert hi["CV-201-IDL-11"] == 100
    assert hi["CV-201-MOT"] == 100
    assert hi["CV-201"] == hi["CV-201-IDL-12"] == hi["PLT-01"]  # el peor manda
    assert hi["CV-201-GBX"] is None  # sin sensores

    with session_factory() as s:
        (rec,) = s.scalars(select(Recommendation)).all()
        assert rec.asset.code == "CV-201-IDL-12"
        assert rec.rule_code == "MR-IDL-REPLACE"
        assert rec.failure_mode.code == "FM-IDL-BEARING"  # heredado de la subunidad CV-201-IDL
        # Sube ~20 °C/h y le faltan ~5 °C para la zona crítica: el plazo se adelanta de 72 h a minutos.
        assert rec.due_by.replace(tzinfo=UTC) - NOW < timedelta(hours=1)
        assert "zona crítica" in rec.reason
        idler = s.scalar(select(Asset).where(Asset.code == "CV-201-IDL-12"))
        (detail,) = idler.health_details["indicators"]
        assert detail["indicator"] == "HI-IDL-TEMP" and detail["trend_per_h"] > 15
        assert s.scalar(select(func.count()).select_from(HealthHistory)) > 40

    types = [p["type"] for p in engine.published]
    assert types.count("recommendation") == 1 and "health" in types


def test_no_duplicates_escalation_and_dismiss_suppression(session_factory, engine) -> None:
    write_hour(session_factory, NOW)
    engine.cycle(NOW)
    engine.cycle(NOW + timedelta(seconds=30))
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Recommendation)) == 1

    # Empeora: la misma recomendación sube a urgente (no se crea otra).
    write_hour(session_factory, NOW + timedelta(minutes=60), hot_delta_end=45.0)
    engine.cycle(NOW + timedelta(minutes=60))
    with session_factory() as s:
        (rec,) = s.scalars(select(Recommendation)).all()
        assert (rec.priority, rec.rule_code) == ("urgent", "MR-IDL-URGENT")
        rec.status = "dismissed"
        rec.updated_at = NOW + timedelta(minutes=60)
        s.commit()

    engine.cycle(NOW + timedelta(minutes=61))
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Recommendation)) == 1  # descartada: no se repite


def test_stopped_conveyor_keeps_last_health(session_factory, engine) -> None:
    write_hour(session_factory, NOW)
    before = engine.cycle(NOW)["CV-201-IDL-12"]
    # Correa detenida: el polín se enfría, pero eso no significa que se reparó.
    write_hour(session_factory, NOW + timedelta(minutes=60), hot_delta_end=0.0, running=0.0)
    after = engine.cycle(NOW + timedelta(minutes=60))["CV-201-IDL-12"]
    assert after == before
    with session_factory() as s:
        idler = s.scalar(select(Asset).where(Asset.code == "CV-201-IDL-12"))
        assert idler.health_details["indicators"][0]["stale"] is True


def test_condition_normalized_flags_recommendation(session_factory, engine) -> None:
    write_hour(session_factory, NOW)
    engine.cycle(NOW)
    write_hour(session_factory, NOW + timedelta(minutes=60), hot_delta_end=0.0)
    hi = engine.cycle(NOW + timedelta(minutes=60))
    assert hi["CV-201-IDL-12"] == 100
    with session_factory() as s:
        (rec,) = s.scalars(select(Recommendation)).all()
        assert rec.status.value == "open" and rec.condition_active is False


def test_twin_api(client: TestClient, session_factory, engine) -> None:
    write_hour(session_factory, NOW)
    engine.cycle(NOW - timedelta(minutes=1))
    engine.cycle(NOW)

    health = client.get("/api/v1/assets/CV-201-IDL/health").json()
    assert health["worst_child"] == "CV-201-IDL-12"
    assert health["children"][0]["code"] == "CV-201-IDL-12"  # ordenados de peor a mejor
    idler = client.get("/api/v1/assets/CV-201-IDL-12/health").json()
    assert idler["indicators"][0]["indicator"] == "HI-IDL-TEMP"

    history = client.get("/api/v1/assets/CV-201/health/history").json()
    assert len(history) == 2

    recs = client.get("/api/v1/recommendations", params={"asset": "CV-201"}).json()
    assert [r["asset_code"] for r in recs] == ["CV-201-IDL-12"]
    assert recs[0]["failure_mode"] == "Rodamiento de polín trabado o sobrecalentado"
    rid = recs[0]["id"]

    upd = client.patch(f"/api/v1/recommendations/{rid}",
                       json={"status": "accepted", "user": "planificador", "note": "OT 4512"}).json()
    assert (upd["status"], upd["note"], upd["updated_by"]) == ("accepted", "OT 4512", "planificador")
    assert len(client.get("/api/v1/recommendations").json()) == 1  # accepted sigue pendiente
    client.patch(f"/api/v1/recommendations/{rid}", json={"status": "done", "user": "planificador"})
    assert client.get("/api/v1/recommendations").json() == []
    assert len(client.get("/api/v1/recommendations", params={"status": "done"}).json()) == 1
    assert client.patch("/api/v1/recommendations/999", json={"status": "done", "user": "x"}).status_code == 404
    assert client.get("/api/v1/assets/NOPE/health").status_code == 404
