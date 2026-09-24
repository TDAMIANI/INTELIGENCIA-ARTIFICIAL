"""Configuración del edge (YAML) con variables de entorno: ${VAR} o ${VAR:-valor_por_defecto}."""

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return expand_env(yaml.safe_load(fh))


def as_bool(value: Any) -> bool:
    """Interpreta booleanos que pueden venir como texto desde variables de entorno."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}
    return bool(value)
