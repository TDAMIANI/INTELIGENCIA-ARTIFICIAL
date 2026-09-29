"""Lectura del PLC/SCADA por OPC UA (solo lectura: el edge nunca escribe en el control).

Da el contexto operativo que necesitan las alarmas y el gemelo: si la correa está en marcha,
su velocidad, la corriente del motor y la carga. Publica en el mismo formato de telemetría
que el resto (mvt/{site}/{sensor}/telemetry).
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mvedge.pipelines import publish_telemetry

log = logging.getLogger("mvedge.plc")


@dataclass(frozen=True)
class PlcConfig:
    endpoint: str
    sensor: str  # código del sensor en plant.yaml (p. ej. CV-201-PLC)
    tags: dict[str, str]  # métrica -> NodeId (p. ej. running -> ns=2;s=CV201.running)
    interval_s: float = 1.0
    timeout_s: float = 5.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlcConfig":
        return cls(d["endpoint"], d["sensor"], dict(d["tags"]), float(d.get("interval_s", 1.0)),
                   float(d.get("timeout_s", 5.0)))


def to_number(value: Any) -> float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    return None


class PlcReader:
    """Lee los tags periódicamente y publica; se reconecta solo si se corta la comunicación."""

    def __init__(self, cfg: PlcConfig, site: str, publish: Callable[[str, str], None],
                 client_factory: Callable[[str], Any] | None = None):
        self.cfg = cfg
        self.site = site
        self.publish = publish
        self.client_factory = client_factory or _sync_client

    def read_once(self, client: Any) -> dict[str, Any]:
        values = {}
        for metric, nid in self.cfg.tags.items():
            number = to_number(client.get_node(nid).read_value())
            if number is not None:
                values[metric] = number
        if values:
            publish_telemetry(self.publish, self.site, self.cfg.sensor, datetime.now(UTC), values)
        return values

    def run(self, stop: threading.Event) -> None:  # pragma: no cover - bucle de reconexión
        while not stop.is_set():
            client = None
            try:
                client = self.client_factory(self.cfg.endpoint)
                client.connect()
                log.info("Conectado al PLC %s", self.cfg.endpoint)
                while not stop.is_set():
                    self.read_once(client)
                    stop.wait(self.cfg.interval_s)
            except Exception as exc:  # noqa: BLE001 - PLC caído, red cortada, nodo inexistente...
                log.warning("Sin comunicación con el PLC (%s); reintento en 5 s", exc)
                stop.wait(5)
            finally:
                if client is not None:
                    try:
                        client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass


def _sync_client(endpoint: str) -> Any:
    from asyncua.sync import Client

    return Client(endpoint, timeout=5)
