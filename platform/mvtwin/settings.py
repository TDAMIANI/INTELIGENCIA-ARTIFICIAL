from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MVT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://mvt:mvt@localhost:5432/mvt"
    plant_config: Path = REPO_ROOT / "config" / "plant.yaml"


settings = Settings()
