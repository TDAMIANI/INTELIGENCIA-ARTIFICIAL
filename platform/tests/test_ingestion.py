import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mvtwin.ingestion.parser import parse_telemetry
from mvtwin.ingestion.rules import AlarmEngine, load_rules
from mvtwin.ingestion.service import Ingestor
from mvtwin.models import Alarm, AlarmSeverity, AlarmStatus, Telemetry
from mvtwin.seed import load_plant_config
from mvtwin.settings import settings

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def msg(sensor: str, values: dict, ts: datetime) -> tuple[str, bytes]:
    payload = {"ts": ts.isoformat(), "sensor": sensor, "values": values, "simulated": True}
    return f"mvt/mina-demo/{sensor}/telemetry", json.dumps(payload).encode()


def make_ingestor(factory: sessionmaker[Session]) -> tuple[Ingestor, list]:
    published: list = []
    rules = load_rules(load_plant_config(settings.plant_config))
    ingestor = Ingestor(factory, AlarmEngine(rules), lambda t, p: published.append((t, p)), "mina-demo")
    return ingestor, published


def test_parse_scalar_nested_and_boolean() -> None:
    topic, payload = msg("S1", {"running": True, "v": 2, "t": {"A": 1.5, "B": "x"}, "note": "txt"}, T0)
    parsed = parse_telemetry(topic, payload)
    assert parsed.sensor_code == "S1"
    assert parsed.metrics == {"running": {None: 1.0}, "v": {None: 2.0}, "t": {"A": 1.5}}


def test_stores_scalar_and_per_asset_metrics(session_factory) -> None:
    ingestor, _ = make_ingestor(session_factory)
    temps = {f"CV-201-IDL-{i:02d}": 30.0 for i in range(1, 41)}
    ingestor.handle(*msg("CV-201-TH-01", {"max_temp_c": temps}, T0))
    ingestor.handle(*msg("CV-201-PLC", {"running": True, "belt_speed_mps": 3.5}, T0))
    # Reenvío duplicado (QoS 1): no debe duplicar filas ni fallar.
    ingestor.handle(*msg("CV-201-PLC", {"running": True, "belt_speed_mps": 3.5}, T0))
    ingestor.handle(*msg("NO-EXISTE", {"x": 1}, T0))
    ingestor.handle("mvt/mina-demo/CV-201-PLC/telemetry", b"no es json")
    assert ingestor.flush() == 40 + 2 + 2
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Telemetry)) == 42


def test_idler_hotspot_alarm_lifecycle(session_factory) -> None:
    ingestor, published = make_ingestor(session_factory)

    def thermal(hot: float, i: int) -> None:
        temps = {f"CV-201-IDL-{n:02d}": 30.0 for n in range(1, 41)}
        temps["CV-201-IDL-12"] = hot
        ingestor.handle(*msg("CV-201-TH-01", {"max_temp_c": temps}, T0 + timedelta(seconds=i)))

    for i in range(3):
        thermal(50.0, i)  # +20 °C -> warning (confirmada en 3 muestras)
    for i in range(3, 6):
        thermal(70.0, i)  # +40 °C -> escalada a critical
    for i in range(6, 9):
        thermal(32.0, i)  # normalizado -> se cierra

    assert [p["event"] for _, p in published] == ["raised", "escalated", "cleared"]
    assert {t for t, _ in published} == {"mvt/mina-demo/alarms"}
    last = published[-1][1]["alarm"]
    assert last["asset_code"] == "CV-201-IDL-12"
    assert last["peak_value"] == 40.0

    with session_factory() as s:
        (alarm,) = s.scalars(select(Alarm)).all()
        assert alarm.status is AlarmStatus.cleared
        assert alarm.severity is AlarmSeverity.critical
        assert alarm.rule_code == "IDL-HOTSPOT"


def test_active_alarm_survives_restart(session_factory) -> None:
    ingestor, _ = make_ingestor(session_factory)
    for i in range(3):
        ingestor.handle(*msg("CV-201-MOT-VIB", {"vibration_rms_mm_s": 5.0}, T0 + timedelta(seconds=i)))

    restarted, published = make_ingestor(session_factory)
    for i in range(3, 6):
        restarted.handle(*msg("CV-201-MOT-VIB", {"vibration_rms_mm_s": 5.0}, T0 + timedelta(seconds=i)))
    assert published == []  # no se duplica la alarma que ya estaba activa
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Alarm)) == 1
