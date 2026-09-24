"""Servicio de ingesta: MQTT -> TimescaleDB + evaluación de alarmas en tiempo real.

Suscribe a mvt/+/+/telemetry, guarda cada medición en `telemetry` (en lotes),
evalúa las reglas de alarma y publica los cambios de alarmas en mvt/{site}/alarms,
que la API retransmite a los navegadores por WebSocket.

También suscribe a mvt/+/+/event (eventos de visión del edge con su imagen de
evidencia), los guarda en `events` y los republica en mvt/{site}/events.

Uso:  python -m mvtwin.ingestion
"""

import json
import logging
import queue
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session, sessionmaker

from mvtwin.ingestion.parser import InvalidMessage, TelemetryMessage, parse_event, parse_telemetry
from mvtwin.ingestion.rules import AlarmEngine, AlarmTransition, TransitionKind, load_rules
from mvtwin.models import Alarm, AlarmStatus, Asset, Event, EventSeverity, Sensor, Telemetry
from mvtwin.schemas import AlarmOut, EventOut
from mvtwin.seed import load_plant_config

log = logging.getLogger("mvtwin.ingestion")

Publisher = Callable[[str, dict[str, Any]], None]


def alarms_topic(site: str) -> str:
    return f"mvt/{site}/alarms"


def events_topic(site: str) -> str:
    return f"mvt/{site}/events"


class Ingestor:
    """Procesa mensajes de telemetría. Independiente de MQTT para poder testearlo."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: AlarmEngine,
        publish: Publisher,
        site: str,
        batch_size: int = 1000,
    ) -> None:
        self.session_factory = session_factory
        self.engine = engine
        self.publish = publish
        self.site = site
        self.batch_size = batch_size
        self._buffer: list[dict[str, Any]] = []
        self._sensors: dict[str, tuple[int, int, str]] = {}  # code -> (sensor_id, asset_id, asset_code)
        self._assets: dict[str, int] = {}  # code -> asset_id
        self._unknown_logged: set[str] = set()
        self.reload_catalog()
        self._restore_active_alarms()

    # --- catálogo -------------------------------------------------------------------------

    def reload_catalog(self) -> None:
        with self.session_factory() as s:
            self._assets = dict(s.execute(select(Asset.code, Asset.id)).tuples().all())
            rows = s.execute(select(Sensor.code, Sensor.id, Asset.id, Asset.code).join(Sensor.asset)).tuples()
            self._sensors = {code: (sid, aid, acode) for code, sid, aid, acode in rows}
        self._unknown_logged.clear()

    def _restore_active_alarms(self) -> None:
        with self.session_factory() as s:
            active = s.execute(
                select(Alarm.rule_code, Asset.code, Alarm.severity, Alarm.peak_value)
                .join(Alarm.asset)
                .where(Alarm.status == AlarmStatus.active)
            ).tuples()
            for rule_code, asset_code, severity, peak in active:
                self.engine.restore_active(rule_code, asset_code, severity, peak)

    def _warn_unknown(self, kind: str, code: str) -> None:
        if code not in self._unknown_logged:
            self._unknown_logged.add(code)
            log.warning("%s desconocido '%s': se descartan sus datos (¿falta en plant.yaml?)", kind, code)

    # --- procesamiento --------------------------------------------------------------------

    def handle(self, topic: str, payload: bytes | str) -> list[AlarmTransition]:
        if topic.endswith("/event"):
            self.handle_event(topic, payload)
            return []
        try:
            msg = parse_telemetry(topic, payload)
        except InvalidMessage as exc:
            log.warning("%s", exc)
            return []
        sensor = self._sensors.get(msg.sensor_code)
        if sensor is None:
            self._warn_unknown("Sensor", msg.sensor_code)
            return []
        self._buffer_rows(msg, sensor)
        if len(self._buffer) >= self.batch_size:
            self.flush()

        transitions: list[AlarmTransition] = []
        for metric, per_asset in msg.metrics.items():
            values = {code or sensor[2]: v for code, v in per_asset.items()}
            transitions += self.engine.evaluate(msg.sensor_code, metric, msg.ts, values)
        if transitions:
            self._apply_transitions(transitions, sensor_id=sensor[0])
        return transitions

    def handle_event(self, topic: str, payload: bytes | str) -> Event | None:
        try:
            msg = parse_event(topic, payload)
            severity = EventSeverity(msg.severity)
        except (InvalidMessage, ValueError) as exc:
            log.warning("Evento descartado: %s", exc)
            return None
        sensor = self._sensors.get(msg.sensor_code)
        if sensor is None:
            self._warn_unknown("Sensor", msg.sensor_code)
            return None
        asset_id = sensor[1] if msg.asset_code is None else self._assets.get(msg.asset_code)
        if asset_id is None:
            self._warn_unknown("Activo", msg.asset_code or "?")
            return None
        with self.session_factory() as s:
            event = Event(
                ts=msg.ts,
                asset_id=asset_id,
                sensor_id=sensor[0],
                type=msg.type,
                severity=severity,
                message=msg.message,
                value=msg.value,
                snapshot_bucket=msg.snapshot_bucket,
                snapshot_key=msg.snapshot_key,
                data=msg.data,
            )
            s.add(event)
            s.commit()
            s.refresh(event, ["asset", "sensor"])
            log.info("Evento %s en %s: %s", event.type, event.asset.code, event.message)
            self.publish(events_topic(self.site), EventOut.from_model(event).model_dump(mode="json"))
        return event

    def _buffer_rows(self, msg: TelemetryMessage, sensor: tuple[int, int, str]) -> None:
        sensor_id, sensor_asset_id, _ = sensor
        for metric, per_asset in msg.metrics.items():
            for asset_code, value in per_asset.items():
                asset_id = sensor_asset_id if asset_code is None else self._assets.get(asset_code)
                if asset_id is None:
                    self._warn_unknown("Activo", asset_code or "?")
                    continue
                self._buffer.append(
                    {"asset_id": asset_id, "metric": metric, "ts": msg.ts, "sensor_id": sensor_id, "value": value}
                )

    def flush(self) -> int:
        if not self._buffer:
            return 0
        rows, self._buffer = self._buffer, []
        with self.session_factory() as s:
            s.execute(_insert_ignore_duplicates(s), rows)
            s.commit()
        return len(rows)

    def _apply_transitions(self, transitions: list[AlarmTransition], sensor_id: int) -> None:
        with self.session_factory() as s:
            changed: list[tuple[TransitionKind, Alarm]] = []
            for t in transitions:
                asset_id = self._assets.get(t.asset_code)
                if asset_id is None:
                    continue
                if t.kind is TransitionKind.raised:
                    alarm = Alarm(
                        asset_id=asset_id,
                        sensor_id=sensor_id,
                        rule_code=t.rule.code,
                        metric=t.rule.metric,
                        severity=t.severity,
                        status=AlarmStatus.active,
                        message=t.message,
                        value=t.value,
                        peak_value=t.peak_value,
                        threshold=t.threshold,
                        raised_at=t.ts,
                    )
                    s.add(alarm)
                else:
                    alarm = s.scalar(
                        select(Alarm).where(
                            Alarm.asset_id == asset_id,
                            Alarm.rule_code == t.rule.code,
                            Alarm.status == AlarmStatus.active,
                        )
                    )
                    if alarm is None:
                        continue
                    alarm.peak_value = t.peak_value
                    if t.kind is TransitionKind.escalated:
                        alarm.severity = t.severity
                        alarm.threshold = t.threshold
                        alarm.message = t.message
                    else:
                        alarm.status = AlarmStatus.cleared
                        alarm.cleared_at = t.ts
                changed.append((t.kind, alarm))
                log.info("Alarma %s: %s [%s] %s", t.kind.value, t.asset_code, t.severity.value, t.message)
            s.commit()
            for kind, alarm in changed:
                s.refresh(alarm, ["asset"])
                self.publish(
                    alarms_topic(self.site),
                    {"event": kind.value, "alarm": AlarmOut.from_model(alarm).model_dump(mode="json")},
                )


def _insert_ignore_duplicates(session: Session):
    """INSERT que ignora mediciones repetidas (p. ej. reenvíos de MQTT con QoS 1)."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(Telemetry).on_conflict_do_nothing()
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(Telemetry).on_conflict_do_nothing()
    return insert(Telemetry)


def run() -> None:  # pragma: no cover - glue con MQTT, se prueba con docker compose
    import paho.mqtt.client as mqtt

    from mvtwin.db import SessionLocal
    from mvtwin.settings import settings

    config = load_plant_config(settings.plant_config)
    site = config.get("site", "default")
    inbox: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=100_000)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mvtwin-ingestion")

    def on_connect(c, _u, _f, reason_code, _p):
        log.info("Conectado a MQTT %s:%s (%s)", settings.mqtt_host, settings.mqtt_port, reason_code)
        c.subscribe([("mvt/+/+/telemetry", 1), ("mvt/+/+/event", 1)])

    def on_message(_c, _u, msg):
        try:
            inbox.put_nowait((msg.topic, msg.payload))
        except queue.Full:
            log.error("Cola de ingesta llena: se descarta un mensaje de %s", msg.topic)

    client.on_connect = on_connect
    client.on_message = on_message

    def publish(topic: str, payload: dict[str, Any]) -> None:
        client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)

    ingestor = Ingestor(SessionLocal, AlarmEngine(load_rules(config)), publish, site)
    log.info("Catálogo: %d sensores, %d activos, %d reglas", len(ingestor._sensors), len(ingestor._assets),
             len(ingestor.engine.rules))

    for attempt in range(1, 31):
        try:
            client.connect(settings.mqtt_host or "localhost", settings.mqtt_port, keepalive=30)
            break
        except OSError as exc:
            log.warning("Broker no disponible (%s), reintento %d/30", exc, attempt)
            time.sleep(2)
    else:
        raise SystemExit("No se pudo conectar al broker MQTT")
    client.loop_start()

    last_flush = time.monotonic()
    last_reload = time.monotonic()
    try:
        while True:
            try:
                topic, payload = inbox.get(timeout=0.5)
                ingestor.handle(topic, payload)
            except queue.Empty:
                pass
            now = time.monotonic()
            if now - last_flush >= settings.ingestion_flush_s:
                written = ingestor.flush()
                if written:
                    log.debug("%d mediciones guardadas", written)
                last_flush = now
            if now - last_reload >= 60:  # toma sensores/activos nuevos sin reiniciar
                ingestor.reload_catalog()
                last_reload = now
    except KeyboardInterrupt:
        pass
    finally:
        ingestor.flush()
        client.loop_stop()
        client.disconnect()


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("Iniciando ingesta (%s)", datetime.now(UTC).isoformat(timespec="seconds"))
    run()
