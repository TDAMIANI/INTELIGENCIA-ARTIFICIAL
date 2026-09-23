from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mvtwin.models import Asset, Sensor
from mvtwin.seed import load_plant_config, seed
from mvtwin.settings import settings


def test_seed_is_idempotent(session: Session) -> None:
    before = session.scalar(select(func.count(Asset.id)))
    seed(session, load_plant_config(settings.plant_config))
    assert session.scalar(select(func.count(Asset.id))) == before
    # planta + área + correa + 5 subunidades + 40 polines
    assert before == 48
    assert session.scalar(select(func.count(Sensor.id))) == 4


def test_health(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_list_roots_and_children(client: TestClient) -> None:
    roots = client.get("/api/v1/assets").json()
    assert [r["code"] for r in roots] == ["PLT-01"]

    children = client.get("/api/v1/assets", params={"parent": "CV-201"}).json()
    assert [c["code"] for c in children] == [
        "CV-201-BELT",
        "CV-201-GBX",
        "CV-201-IDL",
        "CV-201-MOT",
        "CV-201-PUL",
    ]


def test_tree(client: TestClient) -> None:
    (plant,) = client.get("/api/v1/assets/tree").json()
    conveyor = plant["children"][0]["children"][0]
    assert conveyor["code"] == "CV-201"
    assert conveyor["sensor_count"] == 1
    idlers = next(c for c in conveyor["children"] if c["code"] == "CV-201-IDL")
    assert len(idlers["children"]) == 40
    assert idlers["children"][11]["name"] == "Polín 12"


def test_detail_includes_sensors_and_failure_modes(client: TestClient) -> None:
    body = client.get("/api/v1/assets/CV-201-MOT").json()
    assert body["parent_code"] == "CV-201"
    assert body["attributes"]["rated_current_a"] == 95
    assert [s["code"] for s in body["sensors"]] == ["CV-201-MOT-VIB"]
    assert {fm["code"] for fm in body["failure_modes"]} == {"FM-MOT-BEARING", "FM-MOT-IMBALANCE"}


def test_detail_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/assets/NOPE").status_code == 404


def test_create_asset(client: TestClient) -> None:
    payload = {"code": "CV-201-TAKEUP", "name": "Tensor", "level": "subunit", "parent_code": "CV-201"}
    resp = client.post("/api/v1/assets", json=payload)
    assert resp.status_code == 201
    assert resp.json()["parent_code"] == "CV-201"

    assert client.post("/api/v1/assets", json=payload).status_code == 409
    bad_parent = {**payload, "code": "X-1", "parent_code": "NOPE"}
    assert client.post("/api/v1/assets", json=bad_parent).status_code == 404
    bad_level = {**payload, "code": "X-2", "level": "galaxy"}
    assert client.post("/api/v1/assets", json=bad_level).status_code == 422
