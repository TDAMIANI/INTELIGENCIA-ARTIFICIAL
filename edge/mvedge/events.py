"""Eventos con evidencia: la imagen se sube a S3 y el evento se publica por MQTT.

Tópico:  mvt/{site}/{sensor_code}/event
Payload: {"ts", "sensor", "type", "asset", "severity", "message", "value",
          "snapshot": {"bucket", "key"} | null, "data": {...}}
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import cv2
import numpy as np

log = logging.getLogger("mvedge.events")


@dataclass(frozen=True)
class Event:
    sensor: str
    type: str
    severity: str
    message: str
    asset: str | None = None
    value: float | None = None
    data: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


def event_topic(site: str, sensor: str) -> str:
    return f"mvt/{site}/{sensor}/event"


def snapshot_key(site: str, event: Event) -> str:
    stamp = event.ts.strftime("%Y%m%dT%H%M%S%fZ")
    asset = event.asset or event.sensor
    return f"{site}/{event.sensor}/{event.ts:%Y/%m/%d}/{stamp}_{event.type}_{asset}.jpg"


class SnapshotStore:
    """Almacenamiento S3 (SeaweedFS en desarrollo, cualquier S3 en producción)."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(retries={"max_attempts": 2}, connect_timeout=3, read_timeout=10),
        )
        self._bucket_ready = False

    def put_jpeg(self, key: str, image_bgr: np.ndarray) -> None:
        ok, buf = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("No se pudo codificar la imagen de evidencia")
        if not self._bucket_ready:
            try:
                self.s3.head_bucket(Bucket=self.bucket)
            except Exception:  # noqa: BLE001 - el bucket no existe todavía
                self.s3.create_bucket(Bucket=self.bucket)
            self._bucket_ready = True
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=buf.tobytes(), ContentType="image/jpeg")


class EventPublisher:
    """Publica eventos con anti-rebote: el mismo (sensor, tipo, activo) no se repite antes de `cooldown_s`."""

    def __init__(
        self,
        site: str,
        publish: Callable[[str, str], None],
        store: SnapshotStore | None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.site = site
        self.publish = publish
        self.store = store
        self.clock = clock
        self._last: dict[tuple[str, str, str | None], float] = {}

    def emit(self, event: Event, snapshot: np.ndarray | None, cooldown_s: float) -> bool:
        key = (event.sensor, event.type, event.asset)
        now = self.clock()
        if key in self._last and now - self._last[key] < cooldown_s:
            return False
        self._last[key] = now

        snapshot_ref = None
        if snapshot is not None and self.store is not None:
            s3_key = snapshot_key(self.site, event)
            try:
                self.store.put_jpeg(s3_key, snapshot)
                snapshot_ref = {"bucket": self.store.bucket, "key": s3_key}
            except Exception as exc:  # noqa: BLE001 - sin S3 igual se publica el evento
                log.warning("No se pudo guardar la evidencia en S3: %s", exc)

        payload = {
            "ts": event.ts.isoformat(timespec="milliseconds"),
            "sensor": event.sensor,
            "type": event.type,
            "asset": event.asset,
            "severity": event.severity,
            "message": event.message,
            "value": event.value,
            "snapshot": snapshot_ref,
            "data": event.data,
        }
        self.publish(event_topic(self.site, event.sensor), json.dumps(payload, ensure_ascii=False))
        log.info("Evento %s [%s] %s", event.type, event.severity, event.message)
        return True
