from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MVT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://mvt:mvt@localhost:5432/mvt"
    plant_config: Path = REPO_ROOT / "config" / "plant.yaml"
    # Broker MQTT. Si no se define, la API funciona sin WebSocket en vivo.
    mqtt_host: str | None = None
    mqtt_port: int = 1883
    ingestion_flush_s: float = 1.0
    # Almacenamiento S3 de las imágenes de evidencia (lo usa la API para servirlas).
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    # Autenticación (ver mvtwin/auth.py). Desactivada por defecto para desarrollo.
    auth_enabled: bool = False
    auth_mode: str = "local"  # local | oidc
    auth_secret: str | None = None  # firma de los tokens locales (>= 32 caracteres)
    auth_users_file: Path | None = None
    auth_token_ttl_min: int = 720  # un turno de 12 h
    oidc_jwks_url: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_roles_claim: str = "realm_access.roles"  # formato de Keycloak

    # CMMS: si se define, al aceptar una recomendación se crea la orden por webhook.
    cmms_webhook_url: str | None = None

    # Notificaciones (mvtwin.integrations.notify)
    dashboard_url: str | None = None  # para incluir links en los avisos
    notify_cooldown_min: float = 15.0
    notify_webhook_url: str | None = None  # Teams / Slack / Google Chat
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    smtp_from: str = "minevision@localhost"
    notify_email_to: str | None = None  # lista separada por comas


settings = Settings()
