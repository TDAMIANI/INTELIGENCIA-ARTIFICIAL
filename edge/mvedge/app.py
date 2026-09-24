"""Aplicación del gateway edge: un hilo por cámara que lee, analiza y publica.

Uso:  python -m mvedge [--config config/edge.yaml]
"""

import argparse
import logging
import threading
import time
from typing import Any

import cv2

from mvedge.belt import BeltConfig
from mvedge.config import as_bool, load_config
from mvedge.detection import RuleEngine, YoloDetector, ZoneRule
from mvedge.events import EventPublisher, SnapshotStore
from mvedge.pipelines import RgbPipeline, ThermalPipeline
from mvedge.sources import FrameSource, MqttFrameSource, RtspSource
from mvedge.thermal import HotspotDetector, rois_from_config

log = logging.getLogger("mvedge")


def build_source(spec: dict[str, Any], client: Any, kind: str) -> FrameSource:
    if spec["type"] == "rtsp":
        return RtspSource(spec["url"])
    if spec["type"] == "mqtt":
        flags = cv2.IMREAD_UNCHANGED if kind == "thermal" else cv2.IMREAD_COLOR
        return MqttFrameSource(client, spec["topic"], flags)
    raise ValueError(f"Tipo de fuente desconocido: {spec['type']}")


def build_pipeline(cam: dict[str, Any], site: str, publish, events: EventPublisher):
    if cam["kind"] == "thermal":
        hs = cam.get("hotspot", {})
        raw = cam.get("radiometric", {})
        return ThermalPipeline(
            site=site,
            sensor=cam["code"],
            rois=rois_from_config(cam["rois"]),
            publish=publish,
            events=events,
            hotspot=HotspotDetector(
                delta_c=float(hs.get("delta_c", 15)),
                confirm=int(hs.get("confirm", 3)),
                neighbours=int(hs.get("neighbours", 3)),
            ),
            cooldown_s=float(hs.get("cooldown_s", 600)),
            raw_scale=float(raw.get("scale", 0.01)),
            raw_offset_c=float(raw.get("offset_c", -273.15)),
        )
    if cam["kind"] == "rgb":
        det_cfg = cam.get("detector")
        detector = None
        if det_cfg and as_bool(det_cfg.get("enabled", True)):
            detector = YoloDetector(det_cfg.get("model", "yolo11n.pt"), float(det_cfg.get("confidence", 0.4)),
                                    det_cfg.get("classes"))
        return RgbPipeline(
            site=site,
            sensor=cam["code"],
            publish=publish,
            events=events,
            belt=BeltConfig(**cam["belt"]) if cam.get("belt") else None,
            detector=detector,
            rules=RuleEngine([ZoneRule.from_dict(r) for r in cam.get("rules", [])]),
            cooldown_s=float(cam.get("cooldown_s", 60)),
        )
    raise ValueError(f"Tipo de cámara desconocido: {cam['kind']}")


def camera_loop(code: str, source: FrameSource, pipeline, interval_s: float, stop: threading.Event) -> None:
    last_warn = 0.0
    while not stop.is_set():
        started = time.monotonic()
        frame = source.read(timeout=5.0)
        if frame is None:
            if started - last_warn > 30:
                log.warning("[%s] sin imágenes", code)
                last_warn = started
            continue
        try:
            pipeline.process(frame)
        except Exception:  # noqa: BLE001 - una imagen mala no debe tirar el hilo de la cámara
            log.exception("[%s] error procesando imagen", code)
        stop.wait(max(0.0, interval_s - (time.monotonic() - started)))


def run(config_path: str) -> None:  # pragma: no cover - glue, se prueba con docker compose
    import paho.mqtt.client as mqtt

    cfg = load_config(config_path)
    site = cfg["site"]
    mq = cfg.get("mqtt", {})
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.get("gateway_id", "mvedge"))
    # Si se corta la red, paho guarda los mensajes QoS 1 en memoria y los reenvía al reconectar.
    client.max_queued_messages_set(int(mq.get("max_queued", 10000)))
    client.on_connect = lambda c, _u, _f, rc, _p: log.info("Conectado a MQTT (%s)", rc)
    client.connect_async(mq.get("host", "localhost"), int(mq.get("port", 1883)), keepalive=30)
    client.loop_start()

    def publish(topic: str, payload: str) -> None:
        client.publish(topic, payload, qos=1)

    s3 = cfg.get("s3")
    store = SnapshotStore(s3["endpoint"], s3["access_key"], s3["secret_key"], s3["bucket"]) if s3 else None
    events = EventPublisher(site, publish, store)

    stop = threading.Event()
    threads = []
    for cam in cfg["cameras"]:
        if not as_bool(cam.get("enabled", True)):
            continue
        source = build_source(cam["source"], client, cam["kind"])
        pipeline = build_pipeline(cam, site, publish, events)
        t = threading.Thread(
            target=camera_loop,
            args=(cam["code"], source, pipeline, float(cam.get("interval_s", 1.0)), stop),
            name=cam["code"],
            daemon=True,
        )
        t.start()
        threads.append(t)
        log.info("Cámara %s (%s) iniciada", cam["code"], cam["kind"])
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        client.loop_stop()
        client.disconnect()


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Gateway edge de MineVision Twin")
    parser.add_argument("--config", default="config/edge.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(args.config)
