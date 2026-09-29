import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mvtwin.api import events as events_api
from mvtwin.api.live import channel_for_topic
from mvtwin.ingestion.parser import InvalidMessage, parse_event
from mvtwin.models import Event, EventSeverity

from test_ingestion import make_ingestor

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def event_msg(sensor: str, ts: datetime = T0, **fields) -> tuple[str, bytes]:
    payload = {"ts": ts.isoformat(), "sensor": sensor, "type": "thermal_hotspot", "severity": "critical",
               "message": "Polín caliente", "value": 42.0,
               "snapshot": {"bucket": "mvt-evidence", "key": "k/1.jpg"}, "data": {"delta_c": 42.0}} | fields
    return f"mvt/mina-demo/{sensor}/event", json.dumps(payload).encode()


def test_parse_event() -> None:
    ev = parse_event(*event_msg("CV-201-TH-01", asset="CV-201-IDL-12"))
    assert (ev.sensor_code, ev.asset_code, ev.snapshot_key, ev.value) == ("CV-201-TH-01", "CV-201-IDL-12", "k/1.jpg", 42)
    with pytest.raises(InvalidMessage):
        parse_event("mvt/x/S/event", b'{"ts": "2026-01-01T00:00:00"}')  # falta "type"
    with pytest.raises(InvalidMessage):
        parse_event("mvt/x/S/telemetry", b"{}")


def test_ingests_events_and_publishes(session_factory) -> None:
    ingestor, published = make_ingestor(session_factory)
    ingestor.handle(*event_msg("CV-201-TH-01", asset="CV-201-IDL-12"))
    ingestor.handle(*event_msg("CV-201-RGB-01", type="person_in_zone", snapshot=None))  # activo = el del sensor
    ingestor.handle(*event_msg("CV-201-RGB-01", severity="apocalyptic"))  # severidad inválida
    ingestor.handle(*event_msg("NO-EXISTE"))
    ingestor.handle(*event_msg("CV-201-TH-01", asset="NO-EXISTE"))

    with session_factory() as s:
        stored = s.scalars(select(Event).order_by(Event.id)).all()
        assert [(e.type, e.asset.code) for e in stored] == [
            ("thermal_hotspot", "CV-201-IDL-12"),
            ("person_in_zone", "CV-201-BELT"),
        ]
        assert stored[0].severity is EventSeverity.critical
    assert [t for t, _ in published] == ["mvt/mina-demo/events"] * 2
    assert published[0][1]["snapshot_url"] == f"/api/v1/events/{stored[0].id}/snapshot"
    assert published[1][1]["has_snapshot"] is False


def test_list_events_and_snapshot(client: TestClient, session_factory, monkeypatch) -> None:
    ingestor, _ = make_ingestor(session_factory)
    now = datetime.now(UTC)
    ingestor.handle(*event_msg("CV-201-TH-01", ts=now - timedelta(minutes=5), asset="CV-201-IDL-12"))
    ingestor.handle(*event_msg("CV-201-RGB-01", ts=now, type="person_in_zone", snapshot=None))

    everything = client.get("/api/v1/events").json()
    assert [e["type"] for e in everything] == ["person_in_zone", "thermal_hotspot"]
    idlers = client.get("/api/v1/events", params={"asset": "CV-201-IDL"}).json()
    assert [e["asset_code"] for e in idlers] == ["CV-201-IDL-12"]
    assert client.get("/api/v1/events", params={"type": "person_in_zone"}).json()[0]["sensor_code"] == "CV-201-RGB-01"
    recent = client.get("/api/v1/events", params={"since": (now - timedelta(minutes=1)).isoformat()}).json()
    assert len(recent) == 1

    hot_id, person_id = idlers[0]["id"], everything[0]["id"]

    # Sin S3 configurado
    events_api.s3_client.cache_clear()
    monkeypatch.setattr(events_api.settings, "s3_endpoint", None)
    assert client.get(f"/api/v1/events/{hot_id}/snapshot").status_code == 503

    class FakeS3:
        def get_object(self, Bucket, Key):
            assert (Bucket, Key) == ("mvt-evidence", "k/1.jpg")
            return {"Body": io.BytesIO(b"\xff\xd8jpeg"), "ContentType": "image/jpeg"}

    monkeypatch.setattr(events_api, "s3_client", lambda: FakeS3())
    resp = client.get(f"/api/v1/events/{hot_id}/snapshot")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"\xff\xd8jpeg"
    assert client.get(f"/api/v1/events/{person_id}/snapshot").status_code == 404
    assert client.get("/api/v1/events/9999/snapshot").status_code == 404


def test_events_channel() -> None:
    assert channel_for_topic("mvt/mina-demo/events") == "events"
