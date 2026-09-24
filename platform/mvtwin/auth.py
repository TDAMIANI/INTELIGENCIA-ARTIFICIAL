"""Autenticación y roles.

Dos modos (MVT_AUTH_MODE):
- local: usuarios en un YAML (contraseñas con scrypt) y tokens JWT firmados por la API.
- oidc:  tokens de un proveedor OpenID Connect (Keycloak, Azure AD...), validados con su JWKS.
         Así la planta usa su directorio corporativo sin cambiar el código.

Roles:
- viewer: consulta
- operator: + confirma alarmas
- planner: + gestiona recomendaciones y exporta órdenes de trabajo
- engineer: + edita activos
- admin: todo

Con MVT_AUTH_ENABLED=false (desarrollo y tests) todas las llamadas actúan como un admin local.

Generar el hash de una contraseña:  python -m mvtwin.auth hash
"""

import base64
import getpass
import hashlib
import hmac
import secrets
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt
import yaml
from fastapi import Depends, HTTPException, Request, WebSocket, status

from mvtwin.settings import settings


class Role(str, Enum):
    viewer = "viewer"
    operator = "operator"
    planner = "planner"
    engineer = "engineer"
    admin = "admin"


@dataclass(frozen=True)
class User:
    username: str
    name: str
    roles: tuple[str, ...] = field(default_factory=tuple)

    def has(self, role: Role) -> bool:
        return Role.admin.value in self.roles or role.value in self.roles

    def to_dict(self) -> dict[str, Any]:
        return {"username": self.username, "name": self.name, "roles": list(self.roles)}


DEV_USER = User("dev", "Desarrollo", (Role.admin.value,))

# --- contraseñas (scrypt, biblioteca estándar) ------------------------------------------------

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
                                dklen=len(base64.b64decode(hash_b64)))
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


# --- usuarios locales ------------------------------------------------------------------------


@lru_cache
def _load_users(path: str) -> dict[str, dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    users = {}
    for u in data.get("users", []):
        roles = [Role(r).value for r in u.get("roles", ["viewer"])]
        users[u["username"]] = {"name": u.get("name", u["username"]), "roles": roles, "password_hash": u["password_hash"]}
    return users


def local_users() -> dict[str, dict[str, Any]]:
    if not settings.auth_users_file:
        return {}
    return _load_users(str(settings.auth_users_file))


def _secret() -> str:
    if not settings.auth_secret or len(settings.auth_secret) < 32:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "MVT_AUTH_SECRET debe tener al menos 32 caracteres")
    return settings.auth_secret


def authenticate(username: str, password: str) -> User | None:
    u = local_users().get(username)
    # Siempre se calcula un hash, exista o no el usuario, para no revelar cuáles existen por el tiempo de respuesta.
    ok = verify_password(password, u["password_hash"] if u else _DUMMY_HASH)
    return User(username, u["name"], tuple(u["roles"])) if u and ok else None


_DUMMY_HASH = hash_password(secrets.token_hex(8))


def issue_token(user: User, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    payload = {
        "sub": user.username,
        "name": user.name,
        "roles": list(user.roles),
        "iat": now,
        "exp": now + timedelta(minutes=settings.auth_token_ttl_min),
        "iss": "minevision-twin",
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


# --- validación de tokens --------------------------------------------------------------------


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def _claim(payload: dict[str, Any], dotted: str) -> Any:
    value: Any = payload
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def decode_token(token: str) -> User:
    try:
        if settings.auth_mode == "oidc":
            if not settings.oidc_jwks_url:
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Falta MVT_OIDC_JWKS_URL")
            key = _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                key,
                algorithms=["RS256", "ES256"],
                audience=settings.oidc_audience,
                issuer=settings.oidc_issuer,
                options={"verify_aud": settings.oidc_audience is not None},
            )
            known = {r.value for r in Role}
            roles = tuple(r for r in (_claim(payload, settings.oidc_roles_claim) or []) if r in known)
            name = payload.get("name") or payload.get("preferred_username") or payload["sub"]
            return User(payload.get("preferred_username", payload["sub"]), name, roles or (Role.viewer.value,))
        payload = jwt.decode(token, _secret(), algorithms=["HS256"], issuer="minevision-twin")
        return User(payload["sub"], payload.get("name", payload["sub"]), tuple(payload.get("roles", [])))
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido o vencido",
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def _token_from(headers: Any, query: Any) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # <img> y WebSocket no pueden mandar headers: se acepta el token como parámetro.
    return query.get("access_token")


def current_user(request: Request) -> User:
    if not settings.auth_enabled:
        return DEV_USER
    token = _token_from(request.headers, request.query_params)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta iniciar sesión", headers={"WWW-Authenticate": "Bearer"})
    return decode_token(token)


def require(role: Role):
    """Dependencia de FastAPI: exige el rol (o admin)."""

    def check(user: User = Depends(current_user)) -> User:
        if not user.has(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Se requiere el rol '{role.value}'")
        return user

    return check


def websocket_user(websocket: WebSocket) -> User | None:
    """Usuario de un WebSocket, o None si no está autorizado (el llamador cierra la conexión)."""
    if not settings.auth_enabled:
        return DEV_USER
    token = _token_from(websocket.headers, websocket.query_params)
    if not token:
        return None
    try:
        return decode_token(token)
    except HTTPException:
        return None


def actor_name(user: User, claimed: str | None) -> str:
    """Nombre que queda registrado en una acción: el del token si hay autenticación."""
    return user.name if settings.auth_enabled else (claimed or user.name)


def main() -> None:  # pragma: no cover - utilidad de consola
    if len(sys.argv) >= 2 and sys.argv[1] == "hash":
        pw = getpass.getpass("Contraseña: ")
        if pw != getpass.getpass("Repetir: "):
            raise SystemExit("Las contraseñas no coinciden")
        print(hash_password(pw))
    elif len(sys.argv) >= 2 and sys.argv[1] == "secret":
        print(secrets.token_urlsafe(48))
    else:
        print("Uso: python -m mvtwin.auth hash | secret")


if __name__ == "__main__":  # pragma: no cover
    main()
