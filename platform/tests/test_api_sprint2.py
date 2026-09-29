from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mvtwin.api.live import channel_for_topic, hub
from mvtwin.models import Alarm, AlarmSeverity, AlarmStatus, Asset, Sensor, Telemetry

NOW = datetime.now(UTC).replace(microsecond=0)


def add_series(factory: sessionmaker[Session], asset_code: str, sensor_code: str, metric: str, values: list[float],
               start: datetime, step_s: int = 10) -> None:
    with factory() as s:
        asset_id = s.scalar(select(Asset.id).where(Asset.code == asset_code))
        sensor_id = s.scalar(select(Sensor.id).where(Sensor.code == sensor_code))
        s.add_all(
            Telemetry(asset_id=asset_id, sensor_id=sensor_id, metric=metric,
                      ts=start + timedelta(seconds=i * step_s), value=v)
            for i, v in enumerate(values)
        )
        s.commit()


def add_alarm(factory, asset_code: str, status: AlarmStatus, raised_at: datetime) -> int:
    with factory() as s:
        asset_id = s.scalar(select(Asset.id).where(Asset.code == asset_code))
        alarm = Alarm(asset_id=asset_id, rule_code="R", metric="m", severity=AlarmSeverity.warning, status=status,
                      message="msg", value=1, peak_value=1, threshold=1, raised_at=raised_at)
        s.add(alarm)
        s.commit()
        return alarm.id


def test_raw_and_bucketed_series(client: TestClient, session_factory) -> None:
    start = NOW.replace(second=0) - timedelta(minutes=10)
    add_series(session_factory, "CV-201-MOT", "CV-201-MOT-VIB", "vibration_rms_mm_s",
               [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0], start)
    params = {"asset": "CV-201-MOT", "metric": "vibration_rms_mm_s",
              "start": (start - timedelta(seconds=1)).isoformat(), "end": NOW.isoformat()}

    raw = client.get("/api/v1/telemetry", params=params).json()
    assert [p["value"] for p in raw["points"]] == [1, 2, 3, 4, 5, 6, 7, 8, 9]

    bucketed = client.get("/api/v1/telemetry", params={**params, "bucket": 60}).json()
    assert bucketed["bucket_s"] == 60
    first, second = bucketed["points"]
    assert (first["value"], first["min"], first["max"], first["count"]) == (3.5, 1, 6, 6)
    assert (second["value"], second["count"]) == (8, 3)


def test_series_validation(client: TestClient) -> None:
    base = {"asset": "CV-201-MOT", "metric": "x"}
    assert client.get("/api/v1/telemetry", params={**base, "asset": "NOPE"}).status_code == 404
    bad_range = {**base, "start": NOW.isoformat(), "end": (NOW - timedelta(hours=1)).isoformat()}
    assert client.get("/api/v1/telemetry", params=bad_range).status_code == 400
    too_long = {**base, "start": (NOW - timedelta(days=200)).isoformat()}
    assert client.get("/api/v1/telemetry", params=too_long).status_code == 400


def test_latest_with_subtree(client: TestClient, session_factory) -> None:
    start = NOW - timedelta(minutes=5)
    add_series(session_factory, "CV-201-IDL-01", "CV-201-TH-01", "max_temp_c", [30.0, 31.0], start)
    add_series(session_factory, "CV-201-IDL-02", "CV-201-TH-01", "max_temp_c", [29.0, 55.0], start)
    add_series(session_factory, "CV-201", "CV-201-PLC", "belt_speed_mps", [3.5], start)

    latest = client.get("/api/v1/telemetry/latest", params={"asset": "CV-201-IDL", "subtree": True}).json()
    assert [(v["asset_code"], v["value"]) for v in latest] == [("CV-201-IDL-01", 31.0), ("CV-201-IDL-02", 55.0)]
    assert client.get("/api/v1/telemetry/latest", params={"asset": "CV-201-IDL"}).json() == []


def test_alarm_list_filters_and_ack(client: TestClient, session_factory) -> None:
    idler = add_alarm(session_factory, "CV-201-IDL-12", AlarmStatus.active, NOW - timedelta(minutes=1))
    add_alarm(session_factory, "CV-201-MOT", AlarmStatus.cleared, NOW - timedelta(minutes=5))

    active = client.get("/api/v1/alarms", params={"status": "active"}).json()
    assert [a["id"] for a in active] == [idler]
    in_conveyor = client.get("/api/v1/alarms", params={"asset": "CV-201"}).json()
    assert len(in_conveyor) == 2
    assert client.get("/api/v1/alarms", params={"asset": "CV-201-BELT"}).json() == []

    acked = client.post(f"/api/v1/alarms/{idler}/ack", json={"user": "operador1"}).json()
    assert acked["acknowledged_by"] == "operador1"
    assert acked["status"] == "active"  # confirmar no la cierra
    assert client.post("/api/v1/alarms/999/ack", json={"user": "x"}).status_code == 404


def test_update_and_delete_asset(client: TestClient) -> None:
    resp = client.patch("/api/v1/assets/CV-201-GBX", json={"name": "Reductor Flender", "attributes": {"ratio": 25}})
    assert resp.json()["name"] == "Reductor Flender"
    assert resp.json()["attributes"] == {"ratio": 25}

    assert client.delete("/api/v1/assets/CV-201-IDL").status_code == 204
    assert client.get("/api/v1/assets/CV-201-IDL-05").status_code == 404
    assert client.delete("/api/v1/assets/CV-201-IDL").status_code == 404


def test_channel_mapping() -> None:
    assert channel_for_topic("mvt/mina-demo/alarms") == "alarms"
    assert channel_for_topic("mvt/mina-demo/CV-201-TH-01/telemetry") == "telemetry:CV-201-TH-01"
    assert channel_for_topic("mvt/mina-demo/sim/cmd") is None


def test_websocket_receives_published_alarms(client: TestClient) -> None:
    with client.websocket_connect("/ws/alarms") as ws:
        assert ws.receive_json() == {"type": "subscribed", "channel": "alarms"}
        hub.publish("alarms", {"event": "raised", "alarm": {"id": 1}})
        hub.publish("telemetry:OTRO", {"ignored": True})
        assert ws.receive_json() == {"type": "message", "channel": "alarms",
                                     "data": {"event": "raised", "alarm": {"id": 1}}}
    assert not hub.has_subscribers("alarms")
