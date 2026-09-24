import csv
import io
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from mvtwin import auth
from mvtwin.integrations import cmms
from mvtwin.integrations.notify import Notifier
from mvtwin.models import Asset, Recommendation, RecommendationStatus
from mvtwin.settings import settings

SECRET = "x" * 40


@pytest.fixture
def auth_on(monkeypatch, tmp_path):
    users = {
        "users": [
            {"username": "ana", "name": "Ana Operadora", "roles": ["operator"], "password_hash": auth.hash_password("op-123")},
            {"username": "pablo", "name": "Pablo Planificador", "roles": ["planner"], "password_hash": auth.hash_password("pl-123")},
            {"username": "root", "name": "Admin", "roles": ["admin"], "password_hash": auth.hash_password("ad-123")},
        ]
    }
    path = tmp_path / "users.yaml"
    path.write_text(yaml.safe_dump(users), encoding="utf-8")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "auth_secret", SECRET)
    monkeypatch.setattr(settings, "auth_users_file", path)


def login(client: TestClient, user: str, pw: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": user, "password": pw})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def add_rec(factory, asset_code="CV-201-IDL-12", priority="high", status=RecommendationStatus.open) -> int:
    now = datetime.now(UTC)
    with factory() as s:
        asset = s.scalar(select(Asset).where(Asset.code == asset_code))
        rec = Recommendation(asset_id=asset.id, indicator="HI-IDL-TEMP", rule_code="MR-IDL-REPLACE", priority=priority,
                             status=status, action="Cambiar el polín CV-201-IDL-12 en la próxima parada programada",
                             reason="ΔT 25 °C", due_by=now + timedelta(hours=72), condition_active=True,
                             created_at=now, updated_at=now)
        s.add(rec)
        s.commit()
        return rec.id


# --- autenticación ---------------------------------------------------------------------------


def test_password_hashing() -> None:
    h = auth.hash_password("secreta")
    assert h.startswith("scrypt$") and auth.verify_password("secreta", h)
    assert not auth.verify_password("otra", h)
    assert not auth.verify_password("secreta", "basura")


def test_auth_disabled_by_default(client: TestClient) -> None:
    assert client.get("/api/v1/auth/config").json() == {"enabled": False, "mode": "local"}
    assert client.get("/api/v1/auth/me").json()["roles"] == ["admin"]
    assert client.get("/api/v1/assets").status_code == 200


def test_login_and_protected_routes(client: TestClient, auth_on) -> None:
    assert client.get("/api/v1/assets").status_code == 401
    assert client.get("/health").status_code == 200  # el health check sigue público
    assert client.post("/api/v1/auth/login", json={"username": "ana", "password": "mal"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "nadie", "password": "x"}).status_code == 401

    h = login(client, "ana", "op-123")
    assert client.get("/api/v1/auth/me", headers=h).json() == {"username": "ana", "name": "Ana Operadora", "roles": ["operator"]}
    assert client.get("/api/v1/assets", headers=h).status_code == 200
    token = h["Authorization"].split()[1]
    assert client.get("/api/v1/assets", params={"access_token": token}).status_code == 200  # para <img>
    assert client.get("/api/v1/assets", headers={"Authorization": "Bearer basura"}).status_code == 401


def test_expired_and_forged_tokens(client: TestClient, auth_on) -> None:
    user = auth.User("ana", "Ana", ("admin",))
    expired = auth.issue_token(user, now=datetime.now(UTC) - timedelta(days=2))
    assert client.get("/api/v1/assets", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    forged = jwt.encode({"sub": "ana", "roles": ["admin"], "iss": "minevision-twin",
                         "exp": datetime.now(UTC) + timedelta(hours=1)}, "otra-clave" * 5, algorithm="HS256")
    assert client.get("/api/v1/assets", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_roles_are_enforced(client: TestClient, session_factory, auth_on) -> None:
    rid = add_rec(session_factory)
    op = login(client, "ana", "op-123")
    planner = login(client, "pablo", "pl-123")

    # El operador no puede gestionar recomendaciones ni editar activos.
    assert client.patch(f"/api/v1/recommendations/{rid}", json={"status": "accepted", "user": "x"}, headers=op).status_code == 403
    assert client.patch("/api/v1/assets/CV-201-GBX", json={"name": "X"}, headers=op).status_code == 403
    assert client.get("/api/v1/work-orders/export.csv", headers=op).status_code == 403

    # El planificador sí; y queda registrado el nombre del token, no el que manda el cliente.
    resp = client.patch(f"/api/v1/recommendations/{rid}", json={"status": "accepted", "user": "impostor"}, headers=planner)
    assert resp.status_code == 200
    assert resp.json()["updated_by"] == "Pablo Planificador"
    assert client.patch("/api/v1/assets/CV-201-GBX", json={"name": "X"}, headers=login(client, "root", "ad-123")).status_code == 200


def test_websocket_requires_token(client: TestClient, auth_on) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/alarms") as ws:
            ws.receive_json()
    assert exc.value.code == 4401
    token = login(client, "ana", "op-123")["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/alarms?access_token={token}") as ws:
        assert ws.receive_json()["type"] == "subscribed"


def test_oidc_tokens_with_jwks(client: TestClient, monkeypatch) -> None:
    """Simula Keycloak: token RS256 validado con la clave pública del proveedor."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwks:
        def get_signing_key_from_jwt(self, _token):
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_jwks_url", "https://sso.mina.local/realms/planta/protocol/openid-connect/certs")
    monkeypatch.setattr(settings, "oidc_issuer", "https://sso.mina.local/realms/planta")
    monkeypatch.setattr(settings, "oidc_audience", None)
    monkeypatch.setattr(auth, "_jwks_client", lambda _url: FakeJwks())

    claims = {"sub": "u-1", "preferred_username": "jperez", "name": "Juan Pérez",
              "iss": "https://sso.mina.local/realms/planta", "exp": datetime.now(UTC) + timedelta(hours=1),
              "realm_access": {"roles": ["planner", "offline_access"]}}
    token = jwt.encode(claims, key, algorithm="RS256")
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me == {"username": "jperez", "name": "Juan Pérez", "roles": ["planner"]}
    bad_issuer = jwt.encode(claims | {"iss": "https://otro"}, key, algorithm="RS256")
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_issuer}"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "x", "password": "y"}).status_code == 400


# --- CMMS ------------------------------------------------------------------------------------


def test_work_order_csv_export(client: TestClient, session_factory) -> None:
    add_rec(session_factory, priority="urgent", status=RecommendationStatus.accepted)
    add_rec(session_factory, asset_code="CV-201-MOT", priority="medium", status=RecommendationStatus.open)
    resp = client.get("/api/v1/work-orders/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 1  # por defecto, solo las planificadas
    row = rows[0]
    assert (row["equipo"], row["prioridad_sap"], row["tipo_aviso"]) == ("CV-201-IDL-12", "1", "M2")
    assert row["ubicacion_tecnica"] == "PLT-01/AREA-CHS/CV-201/CV-201-IDL"
    assert len(row["texto_breve"]) <= 40
    all_rows = client.get("/api/v1/work-orders/export.csv", params={"status": ["accepted", "open"]}).content
    assert all_rows.decode("utf-8-sig").count("\r\n") == 3


def test_accept_sends_to_cmms(client: TestClient, session_factory, monkeypatch) -> None:
    rid = add_rec(session_factory)
    sent = []
    monkeypatch.setattr(settings, "cmms_webhook_url", "https://cmms.mina.local/hooks/avisos")
    monkeypatch.setattr(cmms, "http_json_sender", lambda url, payload: sent.append((url, payload)) or {"work_order_ref": "10004512"})
    resp = client.patch(f"/api/v1/recommendations/{rid}", json={"status": "accepted", "user": "pablo"})
    assert resp.json()["work_order_ref"] == "10004512"
    assert sent[0][1]["referencia_mvt"] == f"MVT-REC-{rid}"


def test_cmms_failure_keeps_recommendation_open(client: TestClient, session_factory, monkeypatch) -> None:
    rid = add_rec(session_factory)
    monkeypatch.setattr(settings, "cmms_webhook_url", "https://cmms.mina.local/hooks/avisos")

    def boom(url, payload):
        raise ConnectionError("CMMS caído")

    monkeypatch.setattr(cmms, "http_json_sender", boom)
    resp = client.patch(f"/api/v1/recommendations/{rid}", json={"status": "accepted", "user": "pablo"})
    assert resp.status_code == 502
    with session_factory() as s:
        assert s.get(Recommendation, rid).status is RecommendationStatus.open


# --- notificaciones --------------------------------------------------------------------------


def test_notifier_filters_and_rate_limits() -> None:
    sent, now = [], [0.0]
    n = Notifier([sent.append], cooldown_s=900, base_url="http://tablero", clock=lambda: now[0])
    critical = {"event": "raised", "alarm": {"severity": "critical", "asset_code": "CV-201-MOT", "asset_name": "Motor",
                                             "rule_code": "VIB", "message": "Vibración 9 mm/s"}}
    warning = {"event": "raised", "alarm": critical["alarm"] | {"severity": "warning"}}
    assert n.handle("mvt/s/alarms", warning) is None
    note = n.handle("mvt/s/alarms", critical)
    assert note.title.startswith("ALARMA CRÍTICA") and note.link == "http://tablero/#/alarmas"
    assert n.handle("mvt/s/alarms", critical) is None  # anti-rebote
    now[0] = 901
    assert n.handle("mvt/s/alarms", critical) is not None

    urgent = {"type": "recommendation", "recommendation": {"id": 7, "priority": "urgent", "status": "open",
                                                           "asset_code": "X", "action": "Cambiar", "reason": "r", "due_by": "d"}}
    assert n.handle("mvt/s/twin", urgent).title.startswith("MANTENIMIENTO URGENTE")
    assert n.handle("mvt/s/twin", {"type": "health", "assets": []}) is None
    person = {"severity": "critical", "asset_code": "CV-201-BELT", "type": "person_in_zone", "message": "Persona"}
    assert n.handle("mvt/s/events", person).title.startswith("DETECCIÓN CRÍTICA")
    assert len(sent) == 4


def test_failing_channel_does_not_block_others() -> None:
    sent = []

    def broken(_note):
        raise OSError("SMTP caído")

    n = Notifier([broken, sent.append])
    n.handle("mvt/s/events", {"severity": "critical", "asset_code": "A", "type": "t", "message": "m"})
    assert len(sent) == 1
